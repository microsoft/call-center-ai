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
from openai import (
    AsyncAzureOpenAI,
    AsyncStream,
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
)
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)
from typing import AsyncGenerator, List, Optional, Type, TypeVar
import asyncio


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


ModelType = TypeVar("ModelType", bound=BaseModel)


class SafetyCheckError(Exception):
    pass


@retry(
    reraise=True,
    retry=(
        retry_if_exception_type(APIResponseValidationError)
        | retry_if_exception_type(APIStatusError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_stream(
    messages: List[ChatCompletionMessageParam],
    max_tokens: int,
    tools: Optional[List[ChatCompletionToolParam]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    """
    Returns a stream of completion results.

    Catch errors for a maximum of 3 times. Won't retry on connection errors (like timeouts) as the stream will be already partially consumed.
    """
    extra = {}

    if tools:
        extra["tools"] = tools

    stream: AsyncStream[ChatCompletionChunk] = await _oai.chat.completions.create(
        max_tokens=max_tokens,
        messages=messages,
        model=CONFIG.openai.gpt_model,
        stream=True,
        temperature=0,  # Most focused and deterministic
        **extra,
    )
    async for chunck in stream:
        yield chunck.choices[0].delta


@retry(
    reraise=True,
    retry=(
        retry_if_exception_type(APIConnectionError)
        | retry_if_exception_type(APIResponseValidationError)
        | retry_if_exception_type(APIStatusError)
        | retry_if_exception_type(SafetyCheckError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_sync(
    messages: List[ChatCompletionMessageParam],
    max_tokens: int,
    json_output: bool = False,
) -> str:
    """
    Returns a completion result.

    Catch errors for a maximum of 3 times. This includes `SafetyCheckError`, only for text responses (not JSON).
    """
    extra = {}

    if json_output:
        extra["response_format"] = {"type": "json_object"}

    res = await _oai.chat.completions.create(
        max_tokens=max_tokens,
        messages=messages,
        model=CONFIG.openai.gpt_model,
        temperature=0,  # Most focused and deterministic
        **extra,
    )
    content = res.choices[0].message.content

    if not json_output:
        if not await safety_check(content):
            raise SafetyCheckError()

    return content


@retry(
    reraise=True,
    retry=retry_if_exception_type(ValidationError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_model_sync(
    messages: List[ChatCompletionMessageParam],
    max_tokens: int,
    model: Type[ModelType],
) -> ModelType:
    """
    Returns an object validated against a given model, from a completion result.

    Catch errors for a maximum of 3 times, but not `SafetyCheckError`.
    """
    res = await completion_sync(messages, max_tokens, json_output=True)
    return model.model_validate_json(res)


async def safety_check(text: str) -> bool:
    """
    Returns `True` if the text is safe, `False` otherwise.

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


async def close() -> None:
    """
    Safely close the OpenAI and Content Safety clients.
    """
    await asyncio.gather(_oai.close(), _contentsafety.close())


@retry(
    reraise=True,
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
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
    Returns `True` if the category is safe or the severity is low, `False` otherwise, meaning the category is unsafe.
    """
    detection = next(item for item in res if item.category == category)
    if detection and detection.severity and detection.severity > 2:
        _logger.debug(f"Matched {category} with severity {detection.severity}")
        return False
    return True
