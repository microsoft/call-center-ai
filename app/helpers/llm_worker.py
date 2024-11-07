import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from os import environ
from typing import Any, TypeVar

import tiktoken
from json_repair import repair_json
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from pydantic import ValidationError
from rtclient import (
    AssistantMessageItem,
    InputAudioTranscription,
    InputTextContentPart,
    NoTurnDetection,
    OutputTextContentPart,
    RTClient,
    UserMessageItem,
)
from tenacity import (
    AsyncRetrying,
    retry,
    retry_any,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.helpers.config import CONFIG
from app.helpers.llm_utils import RtclientFunctionDefinition
from app.helpers.logging import logger
from app.helpers.monitoring import tracer
from app.helpers.resources import resources_dir
from app.models.message import (
    MessageModel,
)

environ["TRACELOOP_TRACE_CONTENT"] = str(
    True
)  # Instrumentation logs prompts, completions, and embeddings to span attributes, set to False to lower monitoring costs or to avoid logging PII
OpenAIInstrumentor().instrument()  # Instrument OpenAI

# tiktoken cache
environ["TIKTOKEN_CACHE_DIR"] = resources_dir("tiktoken")

logger.info(
    "Using LLM models %s (realtime) and %s (sequential)",
    CONFIG.llm.realtime.selected().model,
    CONFIG.llm.sequential.selected().model,
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


@tracer.start_as_current_span("llm_completion_realtime")
@asynccontextmanager
async def completion_realtime(
    max_tokens: int,
    messages: list[MessageModel],
    system: list[ChatCompletionSystemMessageParam],
    tools: list[RtclientFunctionDefinition],
) -> AsyncGenerator[RTClient, None]:
    # Create client
    async with CONFIG.llm.realtime.selected().instance() as (client, platform):
        # Build history
        history = _limit_messages(
            context_window=platform.context,
            max_messages=20,  # Quick response
            max_tokens=max_tokens,
            messages=messages,
            model=platform.model,
            system=system,
            tools=tools,
        )  # Limit to 20 messages for quick response and avoid hallucinations

        # Transform system prompts to a single text block
        system_text = "\n".join(
            [
                str(message["content"])
                for message in history
                if message["role"] == "system"
            ]
        )

        # Configure LLM
        await client.configure(
            # Deployment
            model=platform.model,
            # Behavior
            instructions=system_text,
            temperature=platform.temperature,
            tool_choice="auto",
            tools=tools,
            # Input/Output
            input_audio_format="pcm16",
            input_audio_transcription=InputAudioTranscription(model="whisper-1"),
            modalities={"text"},
            turn_detection=NoTurnDetection(),  # Disable native turn detection, it is managed manually
            # Performance
            max_response_output_tokens=160,  # Lowest possible value for 90% of the cases, if not sufficient, retry will be triggered, 100 tokens ~= 75 words, 20 words ~= 1 sentence, 6 sentences ~= 160 tokens
        )

        # Push conversation history
        for message in history:
            if message["role"] == "user":
                text = str(message["content"])
                logger.debug("Sending user history: %s...", text[:20])
                await client.send_item(
                    UserMessageItem(content=[InputTextContentPart(text=text)])
                )
            elif message["role"] == "assistant" and "content" in message:
                text = str(message["content"])
                logger.debug("Sending assistant history: %s...", text[:20])
                await client.send_item(
                    AssistantMessageItem(content=[OutputTextContentPart(text=text)])
                )

        # Return
        yield client


@retry(
    reraise=True,
    retry=retry_if_exception_type(ValidationError),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=0.8, max=8),
)
@tracer.start_as_current_span("llm_completion_sequential")
async def completion_sequential(
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
    res_content: str | None = await _completion_sequential_worker(
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
        return await completion_sequential(
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


async def _completion_sequential_worker(
    system: list[ChatCompletionSystemMessageParam],
    json_output: bool = False,
    max_tokens: int | None = None,
) -> str | None:
    """
    Returns a completion.
    """
    client, platform = await CONFIG.llm.sequential.selected().instance()
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
                    seed=platform.seed,
                    temperature=platform.temperature,
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
    tools: list[Any] = [],
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
