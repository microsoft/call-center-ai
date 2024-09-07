import json
from collections.abc import AsyncGenerator, Callable
from functools import lru_cache
from os import environ
from typing import TypeVar

import tiktoken
from json_repair import repair_json
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
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry,
    retry_any,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from helpers.config import CONFIG
from helpers.config_models.llm import AbstractPlatformModel as LlmAbstractPlatformModel
from helpers.logging import logger
from helpers.monitoring import tracer
from helpers.resources import resources_dir
from models.message import MessageModel

environ["TRACELOOP_TRACE_CONTENT"] = str(
    True
)  # Instrumentation logs prompts, completions, and embeddings to span attributes, set to False to lower monitoring costs or to avoid logging PII
OpenAIInstrumentor().instrument()  # Instrument OpenAI

# tiktoken cache
environ["TIKTOKEN_CACHE_DIR"] = resources_dir("tiktoken")

logger.info(
    "Using LLM models %s (slow) and %s (fast)",
    CONFIG.llm.selected(False).model,
    CONFIG.llm.selected(True).model,
)

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


@tracer.start_as_current_span("llm_completion_stream")
async def completion_stream(
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: list[ChatCompletionToolParam] | None = None,
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
            "%s error, trying with the other LLM backend",
            e.__class__.__name__,
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


# TODO: Refacto, too long (and remove PLR0912 ignore)
async def _completion_stream_worker(  # noqa: PLR0912
    is_fast: bool,
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: list[ChatCompletionToolParam] | None = None,
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
            stream: AsyncStream[
                ChatCompletionChunk
            ] = await client.chat.completions.create(
                **chat_kwargs,
                stream=True,
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
                        "Maximum tokens reached %s, should be fixed", max_tokens
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
                logger.warning("Maximum tokens reached %s, should be fixed", max_tokens)
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
            raise SafetyCheckError("Issue detected in prompt") from e
        raise e

    if maximum_tokens_reached:
        raise MaximumTokensReachedError(f"Maximum tokens reached {max_tokens}")


@retry(
    reraise=True,
    retry=retry_if_exception_type(ValidationError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.8, max=8),
)
@tracer.start_as_current_span("llm_completion_sync")
async def completion_sync(
    res_type: type[T],
    system: list[ChatCompletionSystemMessageParam],
    validation_callback: Callable[[str | None], tuple[bool, str | None, T | None]],
    validate_json: bool = False,
    _previous_result: str | None = None,
    _retries_remaining: int = 3,
    _validation_error: str | None = None,
) -> T | None:
    # Initialize prompts
    messages = system
    if _validation_error:
        messages += [
            ChatCompletionAssistantMessageParam(
                content=_previous_result or "",
                role="assistant",
            ),
            ChatCompletionUserMessageParam(
                content=f"A validation error occurred, please retry: {_validation_error}",
                role="user",
            ),
        ]

    # Generate
    res_content: str | None = await _completion_sync_worker(
        is_fast=False,
        json_output=validate_json,
        system=messages,
    )
    if validate_json and res_content:
        # Try to fix JSON args to catch LLM hallucinations
        # See: https://community.openai.com/t/gpt-4-1106-preview-messes-up-function-call-parameters-encoding/478500
        res_content = repair_json(json_str=res_content)  # pyright: ignore

    # Validate
    is_valid, validation_error, res_object = validation_callback(res_content)
    if not is_valid:  # Retry if validation failed
        if _retries_remaining == 0:
            logger.error("LLM validation error: %s", validation_error)
            return None
        logger.warning(
            "LLM validation error, retrying (%s retries left)",
            _retries_remaining,
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
    max_tokens: int | None = None,
) -> str | None:
    """
    Returns a completion.
    """
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
    choice = None
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
                    raise SafetyCheckError("Issue detected in prompt") from e
                raise e
            choice = res.choices[0]
            if choice.finish_reason == "content_filter":  # Azure OpenAI content filter
                raise SafetyCheckError(
                    f"Issue detected in generation: {choice.message.content}"
                )
            if choice.finish_reason == "length":
                raise MaximumTokensReachedError(f"Maximum tokens reached {max_tokens}")

    return choice.message.content if choice else None


def _limit_messages(  # noqa: PLR0913
    context_window: int,
    max_tokens: int | None,
    messages: list[MessageModel],
    model: str,
    system: list[ChatCompletionSystemMessageParam],
    max_messages: int = 1000,
    tools: list[ChatCompletionToolParam] | None = None,
) -> list[
    ChatCompletionAssistantMessageParam
    | ChatCompletionSystemMessageParam
    | ChatCompletionToolMessageParam
    | ChatCompletionUserMessageParam
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

    logger.info("Using %s/%s messages (%s tokens) as context", counter, total, tokens)
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
        logger.debug("Unknown model %s, using %s encoding", model, encoding_name)
    return len(tiktoken.get_encoding(encoding_name).encode(content))


def _use_llm(
    is_fast: bool,
) -> tuple[AsyncAzureOpenAI | AsyncOpenAI, LlmAbstractPlatformModel]:
    """
    Returns an LLM client and platform model.

    The client is either an Azure OpenAI or an OpenAI client, depending on the configuration.
    """
    return CONFIG.llm.selected(is_fast).instance()
