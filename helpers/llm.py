from azure.ai.contentsafety.aio import ContentSafetyClient
from azure.ai.contentsafety.models import (
    AnalyzeTextOptions,
    AnalyzeTextResult,
    TextCategoriesAnalysis,
    TextCategory,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from helpers.config import CONFIG
from helpers.logging import build_logger
from openai import _types as openaiTypes
from openai import AsyncAzureOpenAI, AsyncStream
from openai.types.chat import ChatCompletionMessage, ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from tenacity import retry, stop_after_attempt, wait_random_exponential
from typing import Any, AsyncGenerator, List, Optional


_logger = build_logger(__name__)

_logger.info(f"Using OpenAI GPT model {CONFIG.openai.gpt_model}")
_oai = AsyncAzureOpenAI(
    api_version="2023-12-01-preview",
    azure_deployment=CONFIG.openai.gpt_deployment,
    azure_endpoint=CONFIG.openai.endpoint,
    # Authentication, either RBAC or API key
    api_key=CONFIG.openai.api_key.get_secret_value() if CONFIG.openai.api_key else None,
    azure_ad_token_provider=(
        get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        if not CONFIG.openai.api_key
        else None
    ),
)

_logger.info(f"Using Content Safety {CONFIG.content_safety.endpoint}")
_contentsafety = ContentSafetyClient(
    credential=AzureKeyCredential(CONFIG.content_safety.access_key.get_secret_value()),
    endpoint=CONFIG.content_safety.endpoint,
)


@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=0.5, max=30))
async def completion_stream(
    messages: List[dict[str, Any]],
    max_tokens: int,
    tools: Optional[List[dict[str, Any]]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    stream: AsyncStream[ChatCompletionChunk] = await _oai.chat.completions.create(
        max_tokens=max_tokens,
        messages=messages,
        model=CONFIG.openai.gpt_model,
        stream=True,
        temperature=0,  # Most focused and deterministic
        tools=tools or openaiTypes.NOT_GIVEN,
    )
    async for chunck in stream:
        yield chunck.choices[0].delta


@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=0.5, max=30))
async def completion_sync(
    messages: List[dict[str, Any]],
    max_tokens: int,
    tools: Optional[List[dict[str, Any]]] = None,
) -> ChatCompletionMessage:
    res = await _oai.chat.completions.create(
        max_tokens=max_tokens,
        messages=messages,
        model=CONFIG.openai.gpt_model,
        temperature=0,  # Most focused and deterministic
        tools=tools or openaiTypes.NOT_GIVEN,
    )
    return res.choices[0].message


async def safety_check(text: str) -> bool:
    """
    Returns True if the text is safe, False otherwise.

    Text can be returned both safe and censored, before containing unsafe content.
    """
    try:
        res = await _contentsafety_analysis(text)
    except HttpResponseError as e:
        _logger.error(f"Failed to run safety check: {e.message}")
        return True  # Assume safe

    if not res:
        _logger.error("Failed to run safety check: No result")
        return True  # Assume safe

    for match in res.blocklists_match or []:
        _logger.debug(f"Matched blocklist item: {match.blocklist_item_text}")
        text = text.replace(
            match.blocklist_item_text, "*" * len(match.blocklist_item_text)
        )

    hate_result = _contentsafety_category_test(
        res.categories_analysis, TextCategory.HATE
    )
    self_harm_result = _contentsafety_category_test(
        res.categories_analysis, TextCategory.SELF_HARM
    )
    sexual_result = _contentsafety_category_test(
        res.categories_analysis, TextCategory.SEXUAL
    )
    violence_result = _contentsafety_category_test(
        res.categories_analysis, TextCategory.VIOLENCE
    )

    safety = hate_result and self_harm_result and sexual_result and violence_result
    _logger.debug(f'Text safety "{safety}" for text: {text}')

    return safety


@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=0.5, max=30))
async def _contentsafety_analysis(text: str) -> AnalyzeTextResult:
    return await _contentsafety.analyze_text(
        AnalyzeTextOptions(
            text=text,
            blocklist_names=CONFIG.content_safety.blocklists,
            halt_on_blocklist_hit=False,
        )
    )


def _contentsafety_category_test(
    res: List[TextCategoriesAnalysis], category: TextCategory
) -> bool:
    """
    Returns True if the category is safe or the severity is low. False otherwise, meaning the category is unsafe.
    """
    detection = next(item for item in res if item.category == category)
    if detection and detection.severity and detection.severity > 2:
        _logger.debug(f"Matched {category} with severity {detection.severity}")
        return False
    return True
