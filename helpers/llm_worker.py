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
from contextlib import asynccontextmanager
from helpers.logging import build_logger
from openai import (
    AsyncAzureOpenAI,
    AsyncStream,
    RateLimitError,
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
_logger.info(f"Using Content Safety {CONFIG.content_safety.endpoint}")

ModelType = TypeVar("ModelType", bound=BaseModel)


class SafetyCheckError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


@retry(
    reraise=True,
    retry=retry_if_exception_type(RateLimitError),
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

    Catch errors for a maximum of 3 times (internal + `RateLimitError`), then raise the error.
    """
    extra = {}

    if tools:
        extra["tools"] = tools

    async with _use_oai() as client:
        stream: AsyncStream[ChatCompletionChunk] = await client.chat.completions.create(
            max_tokens=max_tokens,
            messages=messages,
            model=CONFIG.openai.gpt_model,
            stream=True,
            temperature=0,  # Most focused and deterministic
            **extra,
        )
        async for chunck in stream:
            if chunck.choices:  # Skip empty chunks, happens with GPT-4 Turbo
                yield chunck.choices[0].delta


@retry(
    reraise=True,
    retry=(
        retry_if_exception_type(SafetyCheckError)
        | retry_if_exception_type(RateLimitError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_sync(
    messages: List[ChatCompletionMessageParam],
    max_tokens: int,
    json_output: bool = False,
) -> Optional[str]:
    """
    Returns a completion result.

    Catch errors for a maximum of 3 times (internal + `RateLimitError` + `SafetyCheckError`), then raise the error. Safety check is only performed for text responses (= not JSON).
    """
    extra = {}

    if json_output:
        extra["response_format"] = {"type": "json_object"}

    content = None
    async with _use_oai() as client:
        res = await client.chat.completions.create(
            max_tokens=max_tokens,
            messages=messages,
            model=CONFIG.openai.gpt_model,
            temperature=0,  # Most focused and deterministic
            **extra,
        )
        content = res.choices[0].message.content

    if not content:
        return None

    if not json_output:
        await safety_check(content)
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
) -> Optional[ModelType]:
    """
    Returns an object validated against a given model, from a completion result.

    Catch errors for a maximum of 3 times, but not `SafetyCheckError`.
    """
    res = await completion_sync(messages, max_tokens, json_output=True)
    if not res:
        return None
    return model.model_validate_json(res)


async def safety_check(text: str) -> None:
    """
    Raise `SafetyCheckError` if the text is safe, nothing otherwise.

    Text can be returned both safe and censored, before containing unsafe content.
    """
    if not text:
        return
    try:
        res = await _contentsafety_analysis(text)
    except HttpResponseError as e:
        _logger.error(f"Failed to run safety check: {e}")
        return  # Assume safe

    if not res:
        _logger.error("Failed to run safety check: No result")
        return  # Assume safe

    for match in res.blocklists_match or []:
        _logger.debug(f"Matched blocklist item: {match.blocklist_item_text}")
        text = text.replace(
            match.blocklist_item_text, "*" * len(match.blocklist_item_text)
        )

    hate_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.HATE,
        CONFIG.content_safety.category_hate_score,
    )
    self_harm_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.SELF_HARM,
        CONFIG.content_safety.category_self_harm_score,
    )
    sexual_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.SEXUAL,
        CONFIG.content_safety.category_sexual_score,
    )
    violence_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.VIOLENCE,
        CONFIG.content_safety.category_violence_score,
    )

    safety = hate_result and self_harm_result and sexual_result and violence_result
    _logger.debug(f'Text safety "{safety}" for text: {text}')

    if not safety:
        raise SafetyCheckError(
            f"Unsafe content detected, hate={hate_result}, self_harm={self_harm_result}, sexual={sexual_result}, violence={violence_result}: {text}"
        )


@retry(
    reraise=True,
    retry=retry_if_exception_type(HttpResponseError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def _contentsafety_analysis(text: str) -> AnalyzeTextResult:
    async with _use_contentsafety() as client:
        return await client.analyze_text(
            AnalyzeTextOptions(
                blocklist_names=CONFIG.content_safety.blocklists,
                halt_on_blocklist_hit=False,
                output_type="EightSeverityLevels",
                text=text,
            )
        )


def _contentsafety_category_test(
    res: List[TextCategoriesAnalysis],
    category: TextCategory,
    score: int,
) -> bool:
    """
    Returns `True` if the category is safe or the severity is low, `False` otherwise, meaning the category is unsafe.
    """
    if score == 0:
        return True  # No need to check severity

    detection = next(item for item in res if item.category == category)

    if detection and detection.severity and detection.severity > score:
        _logger.debug(f"Matched {category} with severity {detection.severity}")
        return False
    return True


@asynccontextmanager
async def _use_oai() -> AsyncGenerator[AsyncAzureOpenAI, None]:
    client = AsyncAzureOpenAI(
        # Reliability
        max_retries=3,
        timeout=60,
        # Azure deployment
        api_version="2023-12-01-preview",
        azure_deployment=CONFIG.openai.gpt_deployment,
        azure_endpoint=CONFIG.openai.endpoint,
        # Authentication, either RBAC or API key
        api_key=(
            CONFIG.openai.api_key.get_secret_value() if CONFIG.openai.api_key else None
        ),
        azure_ad_token_provider=(
            get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
            if not CONFIG.openai.api_key
            else None
        ),
    )
    yield client
    await client.close()


@asynccontextmanager
async def _use_contentsafety() -> AsyncGenerator[ContentSafetyClient, None]:
    client = ContentSafetyClient(
        # Azure deployment
        endpoint=CONFIG.content_safety.endpoint,
        # Authentication with API key
        credential=AzureKeyCredential(
            CONFIG.content_safety.access_key.get_secret_value()
        ),
    )
    yield client
    await client.close()
