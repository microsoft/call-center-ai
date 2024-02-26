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
from openai import AsyncAzureOpenAI, AsyncStream, RateLimitError, APIError
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_chunk import ChoiceDelta
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)
from typing import AsyncGenerator, List, Optional, Tuple, Type, TypeVar, Union
from httpx import ReadError
from models.message import MessageModel
import tiktoken


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
    is_backup: bool,
    max_tokens: int,
    messages: List[MessageModel],
    system: List[ChatCompletionSystemMessageParam],
    tools: Optional[List[ChatCompletionToolParam]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    """
    Returns a stream of completion results.

    Catch errors for a maximum of 3 times (internal + `RateLimitError`), then raise the error.
    """
    extra = {}

    if tools:
        extra["tools"] = tools

    async with _use_oai(is_backup) as (client, model, context):
        prompt = _prepare_messages(
            context=context,
            messages=messages,
            model=model,
            system=system,
        )

        stream: AsyncStream[ChatCompletionChunk] = await client.chat.completions.create(
            max_tokens=max_tokens,
            messages=prompt,
            model=model,
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
        | retry_if_exception_type(APIError)
        | retry_if_exception_type(RateLimitError)
        | retry_if_exception_type(ReadError)
    ),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.5, max=30),
)
async def completion_sync(
    max_tokens: int,
    messages: List[MessageModel],
    system: List[ChatCompletionSystemMessageParam],
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
    async with _use_oai(False) as (client, model, context):
        prompt = _prepare_messages(
            context=context,
            messages=messages,
            model=model,
            system=system,
        )

        res = await client.chat.completions.create(
            max_tokens=max_tokens,
            messages=prompt,
            model=model,
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
    max_tokens: int,
    messages: List[MessageModel],
    model: Type[ModelType],
    system: List[ChatCompletionSystemMessageParam],
) -> Optional[ModelType]:
    """
    Returns an object validated against a given model, from a completion result.

    Catch errors for a maximum of 3 times, but not `SafetyCheckError`.
    """
    res = await completion_sync(
        json_output=True,
        max_tokens=max_tokens,
        messages=messages,
        system=system,
    )
    if not res:
        return None
    return model.model_validate_json(res)


def _prepare_messages(
    context: int,
    model: str,
    system: List[ChatCompletionSystemMessageParam],
    messages: List[MessageModel],
    max_messages: int = 50,
) -> List[
    Union[
        ChatCompletionAssistantMessageParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionToolMessageParam,
        ChatCompletionUserMessageParam,
    ]
]:
    res: List[
        Union[
            ChatCompletionAssistantMessageParam,
            ChatCompletionSystemMessageParam,
            ChatCompletionToolMessageParam,
            ChatCompletionUserMessageParam,
        ]
    ] = [*system]
    counter = 0
    total = min(len(system) + len(messages), max_messages)

    # Add system messages
    tokens = 0
    for message in system:
        tokens += count_tokens(message.get("content"), model)
        counter += 1

    # Add user messages until the context is reached
    for message in messages:
        tokens += count_tokens(message.content, model)
        if tokens >= context:
            break
        if counter >= max_messages:
            break
        res += message.to_openai()
        counter += 1

    _logger.debug(f"Took {counter}/{total} messages for the context")

    return res


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

    detection = next((item for item in res if item.category == category), None)

    if detection and detection.severity and detection.severity > score:
        _logger.debug(f"Matched {category} with severity {detection.severity}")
        return False
    return True


def count_tokens(content: str, model: str) -> int:
    enc = tiktoken.encoding_for_model(model)
    return len(enc.encode(content))


@asynccontextmanager
async def _use_oai(
    is_backup: bool,
) -> AsyncGenerator[Tuple[AsyncAzureOpenAI, str, int], None]:
    deployment = (
        CONFIG.openai.gpt_deployment
        if not is_backup
        else CONFIG.openai.gpt_backup_deployment
    )
    model = CONFIG.openai.gpt_model if not is_backup else CONFIG.openai.gpt_backup_model
    context = (
        CONFIG.openai.gpt_context if not is_backup else CONFIG.openai.gpt_backup_context
    )

    client = AsyncAzureOpenAI(
        # Reliability
        max_retries=3,
        timeout=60,
        # Azure deployment
        api_version="2023-12-01-preview",
        azure_deployment=deployment,
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

    try:
        yield client, model, context
    finally:
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

    try:
        yield client
    finally:
        await client.close()
