from helpers.config import CONFIG
from helpers.logging import logger, tracer
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AsyncStream,
    BadRequestError,
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
from pydantic import BaseModel, ValidationError
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
from helpers.resources import resources_dir
from models.message import MessageModel
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from os import environ
from typing import AsyncGenerator, Callable, Optional, TypeVar, Union
import json
import tiktoken


environ["TRACELOOP_TRACE_CONTENT"] = str(
    True
)  # Instrumentation logs prompts, completions, and embeddings to span attributes, set to False to lower monitoring costs or to avoid logging PII
OpenAIInstrumentor().instrument()  # Instrument OpenAI

# tiktoken cache
environ["TIKTOKEN_CACHE_DIR"] = resources_dir("tiktoken")

logger.info(
    f"Using LLM models {CONFIG.llm.selected(False).model} (slow) and {CONFIG.llm.selected(True).model} (fast)"
)

ModelType = TypeVar("ModelType", bound=BaseModel)
T = TypeVar("T")


class SafetyCheckError(Exception):
    pass


class MaximumTokensReachedError(Exception):
    pass


_retried_exceptions = [
    APIConnectionError,
    APIResponseValidationError,
    InternalServerError,
    RateLimitError,
]


@tracer.start_as_current_span("completion_stream")
async def completion_stream(
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: Optional[list[ChatCompletionToolParam]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    """
    Returns a stream of completions.

    Completion is first made with the fast LLM, then the slow LLM if the previous fails. Catch errors for a maximum of 3 times (internal + `RateLimitError`). If it fails again, raise the error.
    """
    retryed = AsyncRetrying(
        reraise=True,
        retry=retry_any(
            *[retry_if_exception_type(exception) for exception in _retried_exceptions]
        ),
        stop=stop_after_attempt(3),  # Usage is short-lived, so stop after 3 attempts
        wait=wait_random_exponential(multiplier=0.8, max=8),
    )

    # Try first with primary LLM
    try:
        async for attempt in retryed:
            with attempt:
                async for chunck in _completion_stream_worker(
                    is_fast=not CONFIG.conversation.slow_llm_for_chat,  # Let configuration decide
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
        logger.warning(
            f"{e.__class__.__name__} error, trying with the other LLM backend"
        )

    # Then try more times with backup LLM
    async for attempt in retryed:
        with attempt:
            async for chunck in _completion_stream_worker(
                is_fast=CONFIG.conversation.slow_llm_for_chat,  # Let configuration decide
                max_tokens=max_tokens,
                messages=messages,
                system=system,
                tools=tools,
            ):
                yield chunck


async def _completion_stream_worker(
    is_fast: bool,
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: Optional[list[ChatCompletionToolParam]] = None,
) -> AsyncGenerator[ChoiceDelta, None]:
    """
    Returns a stream of completions.
    """
    client, platform = _use_llm(is_fast)
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
    maximum_tokens_reached = False

    try:
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
                choice = choices[0]
                delta = choice.delta
                if (
                    choice.finish_reason == "content_filter"
                ):  # Azure OpenAI content filter
                    raise SafetyCheckError(f"Issue detected in text: {delta.content}")
                if choice.finish_reason == "length":
                    logger.warning(
                        f"Maximum tokens reached {max_tokens}, should be fixed"
                    )
                    maximum_tokens_reached = True
                if delta:
                    yield delta

        else:  # Non-streaming, emulate streaming with a single completion
            completion: ChatCompletion = await client.chat.completions.create(
                **chat_kwargs
            )
            choice = completion.choices[0]
            if choice.finish_reason == "content_filter":  # Azure OpenAI content filter
                raise SafetyCheckError(
                    f"Issue detected in generation: {choice.message.content}"
                )
            if choice.finish_reason == "length":
                logger.warning(f"Maximum tokens reached {max_tokens}, should be fixed")
                maximum_tokens_reached = True
            message = choice.message
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
    except BadRequestError as e:
        if e.code == "content_filter":
            raise SafetyCheckError("Issue detected in prompt")
        raise e

    if maximum_tokens_reached:
        raise MaximumTokensReachedError(f"Maximum tokens reached {max_tokens}")


@retry(
    reraise=True,
    retry=retry_if_exception_type(ValidationError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.8, max=8),
)
@tracer.start_as_current_span("completion_sync")
async def completion_sync(
    res_type: type[T],
    system: list[ChatCompletionSystemMessageParam],
    validation_callback: Callable[
        [Optional[str]], tuple[bool, Optional[str], Optional[T]]
    ],
    validate_json: bool = False,
    _previous_result: Optional[str] = None,
    _retries_remaining: int = 3,
    _validation_error: Optional[str] = None,
) -> Optional[T]:
    # Initialize prompts
    messages = system
    if _validation_error:
        messages.append(
            ChatCompletionSystemMessageParam(
                role="system",
                content=f"""
                    A validation error occurred during the previous attempt.

                    # Previous result
                    {_previous_result or "N/A"}

                    # Error details
                    {_validation_error}
                    """,
            )
        )

    # Generate
    res_content = await _completion_sync_worker(
        is_fast=False,
        json_output=validate_json,
        system=messages,
    )

    # Validate
    is_valid, validation_error, res_object = validation_callback(res_content)
    if not is_valid:  # Retry if validation failed
        if _retries_remaining == 0:
            logger.error(f"LLM validation error: {validation_error}")
            return None
        logger.warning(
            f"LLM validation error, retrying ({_retries_remaining} retries left)"
        )
        return await completion_sync(
            res_type=res_type,
            system=system,
            validate_json=validate_json,
            validation_callback=validation_callback,
            _previous_result=res_content,
            _retries_remaining=_retries_remaining - 1,
            _validation_error=validation_error,
        )

    # Return after validation or if failed too many times
    return res_object


async def _completion_sync_worker(
    is_fast: bool,
    system: list[ChatCompletionSystemMessageParam],
    json_output: bool = False,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """
    Returns a completion.
    """
    content = None
    client, platform = _use_llm(is_fast)
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

    # Try more times with fast LLM, if it fails again, raise the error
    retryed = AsyncRetrying(
        reraise=True,
        retry=retry_any(
            *[retry_if_exception_type(exception) for exception in _retried_exceptions]
        ),
        stop=stop_after_attempt(
            10
        ),  # Usage is async and long-lived, so stop after 10 attempts
        wait=wait_random_exponential(multiplier=0.8, max=8),
    )
    async for attempt in retryed:
        with attempt:
            try:
                res = await client.chat.completions.create(
                    max_tokens=max_tokens,
                    messages=prompt,
                    model=platform.model,
                    seed=42,  # Reproducible results
                    temperature=0,  # Most focused and deterministic
                    **extra,
                )
            except BadRequestError as e:
                if e.code == "content_filter":
                    raise SafetyCheckError("Issue detected in prompt")
                raise e
            choice = res.choices[0]
            if choice.finish_reason == "content_filter":  # Azure OpenAI content filter
                raise SafetyCheckError(
                    f"Issue detected in generation: {choice.message.content}"
                )
            if choice.finish_reason == "length":
                raise MaximumTokensReachedError(f"Maximum tokens reached {max_tokens}")

    content = choice.message.content
    return content or None


def _limit_messages(
    context_window: int,
    max_tokens: Optional[int],
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
    max_tokens = max_tokens or 0  # Default

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


@lru_cache  # Cache results in memory as token count is done many times on the same content
def _count_tokens(content: str, model: str) -> int:
    """
    Returns the number of tokens in the content, using the model's encoding.

    If the model is unknown to tiktoken, it uses the GPT-3.5 encoding.
    """
    try:
        encoding_name = tiktoken.encoding_name_for_model(model)
    except KeyError:
        encoding_name = tiktoken.encoding_name_for_model("gpt-3.5")
        logger.debug(f"Unknown model {model}, using {encoding_name} encoding")
    return len(tiktoken.get_encoding(encoding_name).encode(content))


def _use_llm(
    is_fast: bool,
) -> tuple[Union[AsyncAzureOpenAI, AsyncOpenAI], LlmAbstractPlatformModel]:
    """
    Returns an LLM client and platform model.

    The client is either an Azure OpenAI or an OpenAI client, depending on the configuration.
    """
    return CONFIG.llm.selected(is_fast).instance()
