from azure.ai.contentsafety.aio import ContentSafetyClient
from azure.ai.contentsafety.models import (
    AnalyzeTextOptions,
    AnalyzeTextResult,
    TextCategoriesAnalysis,
    TextCategory,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from helpers.config import CONFIG
from contextlib import asynccontextmanager
from helpers.logging import logger, tracer
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AsyncStream,
    InternalServerError,
    RateLimitError,
)
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_chunk import (
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from pydantic import BaseModel, ValidationError, TypeAdapter
from tenacity import (
    AsyncRetrying,
    retry_any,
    retry_if_exception_type,
    retry,
    stop_after_attempt,
    wait_random_exponential,
)
from functools import lru_cache
from helpers.config_models.llm import AbstractPlatformModel as LlmAbstractPlatformModel
from models.message import MessageModel
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from os import environ
from typing import AsyncGenerator, Optional, Tuple, Type, TypeVar, Union
import json
import tiktoken


# Instrument OpenAI
environ["TRACELOOP_TRACE_CONTENT"] = str(
    True
)  # Instrumentation logs prompts, completions, and embeddings to span attributes, set to False to lower monitoring costs or to avoid logging PII
OpenAIInstrumentor(enrich_token_usage=True).instrument()

logger.info(
    f"Using LLM models {CONFIG.llm.selected(False).model} (primary) and {CONFIG.llm.selected(True).model} (backup)"
)
logger.info(f"Using Content Safety {CONFIG.content_safety.endpoint}")

_cache = CONFIG.cache.instance()
ModelType = TypeVar("ModelType", bound=BaseModel)
_retried_exceptions = [
    APIConnectionError,
    APIResponseValidationError,
    InternalServerError,
    RateLimitError,
]


class SafetyCheckError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


@tracer.start_as_current_span("completion_stream")
async def completion_stream(
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: Optional[list[ChatCompletionToolParam]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    """
    Returns a stream of completions.

    Catch errors for a maximum of 3 times (internal + `RateLimitError`). If the error persists, try with the backup LLM. If it fails again, raise the error.
    """
    # Try a first time with primary LLM
    try:
        async for chunck in _completion_stream_worker(
            is_backup=False,
            max_tokens=max_tokens,
            messages=messages,
            system=system,
            tools=tools,
        ):
            yield chunck
        return
    except Exception as e:
        if not any(isinstance(e, exception) for exception in _retried_exceptions):
            raise e
        logger.warning(f"{e.__class__.__name__} error, trying with backup LLM")

    # Try more times with backup LLM, if it fails again, raise the error
    retryed = AsyncRetrying(
        reraise=True,
        retry=retry_any(
            *[retry_if_exception_type(exception) for exception in _retried_exceptions]
        ),
        stop=stop_after_attempt(3),  # Usage is short-lived, so stop after 3 attempts
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async for attempt in retryed:
        with attempt:
            async for chunck in _completion_stream_worker(
                is_backup=True,
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                tools=tools,
            ):
                yield chunck


async def _completion_stream_worker(
    is_backup: bool,
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: Optional[list[ChatCompletionToolParam]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    """
    Returns a stream of completions.
    """
    # Try cache
    cache_key = f"{__name__}-completion_stream-{_llm_key(is_backup)}-{system}-{tools}-{messages}-{max_tokens}"
    cached = await _cache.aget(cache_key)
    if cached:
        try:
            for chunck in TypeAdapter(list[ChoiceDelta]).validate_json(cached):
                yield chunck
            return
        except ValidationError as e:
            logger.debug(f"Parsing error: {e.errors()}")

    # Try live
    to_cache: list[ChoiceDelta] = []
    async with _use_llm(is_backup) as (client, platform):
        extra = {}
        if tools:
            extra["tools"] = tools  # Add tools if any

        prompt = _limit_messages(
            context_window=platform.context,
            max_messages=20,  # Quick response
            max_tokens=max_tokens,
            messages=messages,
            model=platform.model,
            system=system,
            tools=tools,
        )  # Limit to 20 messages for quick response and avoid hallucinations
        chat_kwargs = {
            "max_tokens": max_tokens,
            "messages": prompt,
            "model": platform.model,
            "seed": 42,  # Reproducible results
            "temperature": 0,  # Most focused and deterministic
            **extra,
        }  # Shared kwargs for both streaming and non-streaming

        if platform.streaming:  # Streaming
            stream: AsyncStream[ChatCompletionChunk] = (
                await client.chat.completions.create(
                    **chat_kwargs,
                    stream=True,
                )
            )
            async for chunck in stream:
                choices = chunck.choices
                if (
                    not choices
                ):  # Skip empty choices, happens sometimes with GPT-4 Turbo
                    continue
                delta = choices[0].delta
                yield delta
                to_cache.append(delta)

        else:  # Non-streaming, emulate streaming with a single completion
            completion: ChatCompletion = await client.chat.completions.create(
                **chat_kwargs
            )
            message = completion.choices[0].message
            delta = ChoiceDelta(
                content=message.content,
                role=message.role,
                tool_calls=[
                    ChoiceDeltaToolCall(
                        id=tool.id,
                        index=0,
                        type=tool.type,
                        function=ChoiceDeltaToolCallFunction(
                            arguments=tool.function.arguments,
                            name=tool.function.name,
                        ),
                    )
                    for tool in message.tool_calls or []
                ],
            )
            yield delta
            to_cache.append(delta)

    # Update cache
    await _cache.aset(cache_key, TypeAdapter(list[ChoiceDelta]).dump_json(to_cache))


@tracer.start_as_current_span("completion_sync")
async def completion_sync(
    max_tokens: int,
    system: list[ChatCompletionSystemMessageParam],
    json_output: bool = False,
) -> Optional[str]:
    """
    Returns a completion.

    Catch errors for a maximum of 10 times (internal + `RateLimitError`). If the error persists, try with the backup LLM. If it fails again, raise the error.
    """
    # Try a first time with primary LLM
    try:
        return await _completion_sync_worker(
            is_backup=False,
            max_tokens=max_tokens,
            system=system,
            json_output=json_output,
        )
    except Exception as e:
        if not any(isinstance(e, exception) for exception in _retried_exceptions):
            raise e
        logger.warning(f"{e.__class__.__name__} error, trying with backup LLM")

    # Try more times with backup LLM, if it fails again, raise the error
    retryed = AsyncRetrying(
        reraise=True,
        retry=retry_any(
            *[retry_if_exception_type(exception) for exception in _retried_exceptions]
        ),
        stop=stop_after_attempt(
            10
        ),  # Usage is async and long-lived, so stop after 10 attempts
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async for attempt in retryed:
        with attempt:
            return await _completion_sync_worker(
                is_backup=True,
                max_tokens=max_tokens,
                system=system,
                json_output=json_output,
            )


async def _completion_sync_worker(
    is_backup: bool,
    max_tokens: int,
    system: list[ChatCompletionSystemMessageParam],
    json_output: bool = False,
) -> Optional[str]:
    """
    Returns a completion.
    """
    # Try cache
    cache_key = f"{__name__}-completion_sync-{system}-{max_tokens}"
    cached = await _cache.aget(cache_key)
    if cached:
        return cached.decode()

    # Try live
    content = None
    async with _use_llm(is_backup) as (client, platform):
        extra = {}
        if json_output:
            extra["response_format"] = {"type": "json_object"}

        prompt = _limit_messages(
            context_window=platform.context,
            max_tokens=max_tokens,
            messages=[],
            model=platform.model,
            system=system,
        )

        res = await client.chat.completions.create(
            max_tokens=max_tokens,
            messages=prompt,
            model=platform.model,
            seed=42,  # Reproducible results
            temperature=0,  # Most focused and deterministic
            **extra,
        )
        content = res.choices[0].message.content

    # Update cache
    if content:
        await _cache.aset(cache_key, content.encode())

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
@tracer.start_as_current_span("completion_model_sync")
async def completion_model_sync(
    max_tokens: int,
    model: Type[ModelType],
    system: list[ChatCompletionSystemMessageParam],
) -> Optional[ModelType]:
    """
    Generate a Pydantic model from a completion.

    Catch Pydantic validation errors for a maximum of 3 times, then raise the error.
    """
    res = await completion_sync(
        json_output=True,
        max_tokens=max_tokens,
        system=system,
    )
    if not res:
        return None
    return model.model_validate_json(res)


def _limit_messages(
    context_window: int,
    max_tokens: int,
    messages: list[MessageModel],
    model: str,
    system: list[ChatCompletionSystemMessageParam],
    max_messages: int = 1000,
    tools: Optional[list[ChatCompletionToolParam]] = None,
) -> list[
    Union[
        ChatCompletionAssistantMessageParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionToolMessageParam,
        ChatCompletionUserMessageParam,
    ]
]:
    """
    Returns a list of messages limited by the context size.

    The context size is the maximum number of tokens allowed by the model. The messages are selected from the newest to the oldest, until the context or the maximum number of messages is reached.
    """
    counter = 0
    max_context = context_window - max_tokens
    selected_messages = []
    tokens = 0
    total = min(len(system) + len(messages), max_messages)

    # Add system messages
    for message in system:
        tokens += _count_tokens(json.dumps(message), model)
        counter += 1

    # Add tools
    for tool in tools or []:
        tokens += _count_tokens(json.dumps(tool), model)

    # Add user messages until the available context is reached, from the newest to the oldest
    for message in messages[::-1]:
        openai_message = message.to_openai()
        new_tokens = _count_tokens(
            "".join([json.dumps(x) for x in openai_message]),
            model,
        )
        if tokens + new_tokens >= max_context:
            break
        if counter >= max_messages:
            break
        counter += 1
        selected_messages += openai_message[::-1]
        tokens += new_tokens

    logger.info(f"Using {counter}/{total} messages ({tokens} tokens) as context")
    return [
        *system,
        *selected_messages[::-1],
    ]


@tracer.start_as_current_span("safety_check")
async def safety_check(text: str) -> None:
    """
    Raise `SafetyCheckError` if the text is safe, nothing otherwise.

    Text can be returned both safe and censored, before containing unsafe content.
    """
    if not text:  # Empty text is safe
        return

    try:
        res = await _contentsafety_analysis(text)
    except ServiceRequestError as e:
        logger.error(f"Request error: {e}")
        return  # Assume safe
    except HttpResponseError as e:
        logger.error(f"Response error: {e}")
        return  # Assume safe

    # Replace blocklist items with censored text
    for match in res.blocklists_match or []:
        logger.debug(f"Matched blocklist item: {match.blocklist_item_text}")
        text = text.replace(
            match.blocklist_item_text, "*" * len(match.blocklist_item_text)
        )

    # Check hate category
    hate_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.HATE,
        CONFIG.content_safety.category_hate_score,
    )
    # Check self harm category
    self_harm_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.SELF_HARM,
        CONFIG.content_safety.category_self_harm_score,
    )
    # Check sexual category
    sexual_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.SEXUAL,
        CONFIG.content_safety.category_sexual_score,
    )
    # Check violence category
    violence_result = _contentsafety_category_test(
        res.categories_analysis,
        TextCategory.VIOLENCE,
        CONFIG.content_safety.category_violence_score,
    )

    # True if all categories are safe
    safety = hate_result and self_harm_result and sexual_result and violence_result
    logger.debug(f'Text safety "{safety}" for text: {text}')

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
    """
    Returns the result of the content safety analysis.

    Catch errors for a maximum of 3 times (internal + `HttpResponseError`), then raise the error.
    """
    async with _use_contentsafety() as client:
        res = await client.analyze_text(
            AnalyzeTextOptions(
                blocklist_names=CONFIG.content_safety.blocklists,
                halt_on_blocklist_hit=False,
                output_type="EightSeverityLevels",
                text=text,
            )
        )
    return res


def _contentsafety_category_test(
    res: list[TextCategoriesAnalysis],
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
        logger.debug(f"Matched {category} with severity {detection.severity}")
        return False
    return True


@lru_cache  # Cache results in memory as token count is done many times on the same content
def _count_tokens(content: str, model: str) -> int:
    """
    Returns the number of tokens in the content, using the model's encoding.

    If the model is unknown to TikToken, it uses the GPT-3.5 encoding.
    """
    try:
        encoding_name = tiktoken.encoding_name_for_model(model)
    except KeyError:
        encoding_name = tiktoken.encoding_name_for_model("gpt-3.5")
        logger.warning(f"Unknown model {model}, using {encoding_name} encoding")
    return len(tiktoken.get_encoding(encoding_name).encode(content))


def _llm_key(is_backup: bool) -> str:
    platform = CONFIG.llm.selected(is_backup)
    return f"{platform.model}-{platform.context}"


@asynccontextmanager
async def _use_llm(
    is_backup: bool,
) -> AsyncGenerator[
    Tuple[Union[AsyncAzureOpenAI, AsyncOpenAI], LlmAbstractPlatformModel], None
]:
    """
    Returns an LLM client and platform model.

    The client is either an Azure OpenAI or an OpenAI client, depending on the configuration.
    """
    async with CONFIG.llm.selected(is_backup).instance() as (client, platform):
        yield client, platform


@asynccontextmanager
async def _use_contentsafety() -> AsyncGenerator[ContentSafetyClient, None]:
    """
    Returns a Content Safety client.
    """
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
