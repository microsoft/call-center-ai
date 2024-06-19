from typing import Awaitable, Callable, Optional, Tuple, Type
from azure.communication.callautomation.aio import CallAutomationClient
from helpers.config import CONFIG
from helpers.logging import logger
from models.call import CallStateModel
from models.message import (
    extract_message_style,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    remove_message_action,
    StyleEnum as MessageStyleEnum,
    ToolModel as MessageToolModel,
)
from helpers.call_utils import (
    handle_clear_queue,
    handle_media,
    handle_recognize_text,
    tts_sentence_split,
)
from helpers.llm_tools import LlmPlugins
import asyncio
from helpers.llm_worker import (
    completion_model_sync,
    completion_stream,
    completion_sync,
    MaximumTokensReachedError,
    ModelType,
    safety_check,
    SafetyCheckError,
)
from openai import APIError
from openai.types.chat import ChatCompletionSystemMessageParam
import time


_cache = CONFIG.cache.instance()
_db = CONFIG.database.instance()


async def llm_completion(text: Optional[str], call: CallStateModel) -> Optional[str]:
    """
    Run LLM completion from a system prompt and a Call model.

    If the system prompt is None, no completion will be run and None will be returned. Otherwise, the response of the LLM will be returned.
    """
    logger.info("Running LLM completion")

    if not text:
        return None

    system = _llm_completion_system(text, call)
    content = None

    try:
        content = await completion_sync(
            max_tokens=1000,
            system=system,
        )
    except APIError as e:
        logger.warning(f"OpenAI API call error: {e}")
    except SafetyCheckError as e:
        logger.warning(f"OpenAI safety check error: {e}")

    return content


async def llm_model(
    text: Optional[str], call: CallStateModel, model: Type[ModelType]
) -> Optional[ModelType]:
    """
    Run LLM completion from a system prompt, a Call model, and an expected model type as a return.

    The logic will try its best to return a model of the expected type, but it is not guaranteed. It it fails, `None` will be returned.
    """
    logger.debug("Running LLM model")

    if not text:
        return None

    system = _llm_completion_system(text, call)
    res = None

    try:
        res = await completion_model_sync(
            max_tokens=1000,
            model=model,
            system=system,
        )
    except APIError as e:
        logger.warning(f"OpenAI API call error: {e}")

    return res


def _llm_completion_system(
    system: str, call: CallStateModel
) -> list[ChatCompletionSystemMessageParam]:
    messages = [
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.default_system(call),
            role="system",
        ),
        ChatCompletionSystemMessageParam(
            content=system,
            role="system",
        ),
    ]
    logger.debug(f"Messages: {messages}")
    return messages


async def load_llm_chat(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], None],
    trainings_callback: Callable[[CallStateModel], None],
    _iterations_remaining: int = 3,
) -> CallStateModel:
    """
    Handle the intelligence of the call, including: LLM chat, TTS, and media play.

    Play the loading sound while waiting for the intelligence to be processed. If the intelligence is not processed after few seconds, play the timeout sound. If the intelligence is not processed after more seconds, stop the intelligence processing and play the error sound.

    Returns the updated call model.
    """
    logger.info("Loading LLM chat")

    should_play_sound = True

    async def _tts_callback(text: str, style: MessageStyleEnum) -> None:
        """
        Send back the TTS to the user.
        """
        nonlocal should_play_sound

        try:
            text = await safety_check(text)
        except SafetyCheckError as e:
            logger.warning(f"Unsafe text detected, not playing: {e}")
            return

        should_play_sound = False
        await asyncio.gather(
            handle_recognize_text(
                call=call,
                client=client,
                style=style,
                text=text,
                timeout_error=False,  # Voice will continue, don't trigger
            ),  # First, recognize the next voice
            _db.call_aset(
                call
            ),  # Second, save in DB allowing (1) user to cut off the Assistant and (2) SMS answers to be in order
        )

    # Pointer
    pointer_cache_key = f"{__name__}-load_llm_chat-pointer-{call.call_id}"
    pointer_current = time.time()  # Get system current time
    await _cache.aset(pointer_cache_key, str(pointer_current))

    # Chat
    chat_task = asyncio.create_task(
        _execute_llm_chat(
            call=call,
            client=client,
            post_callback=post_callback,
            use_tools=_iterations_remaining > 0,
            tts_callback=_tts_callback,
        )
    )

    # Loading
    def _loading_task() -> asyncio.Task:
        return asyncio.create_task(asyncio.sleep(loading_timer))

    loading_timer = 5  # Play loading sound every 5 seconds
    loading_task = _loading_task()

    # Timeouts
    soft_timeout_triggered = False
    soft_timeout_task = asyncio.create_task(
        asyncio.sleep(CONFIG.conversation.answer_soft_timeout_sec)
    )
    hard_timeout_task = asyncio.create_task(
        asyncio.sleep(CONFIG.conversation.answer_hard_timeout_sec)
    )

    await handle_media(
        call=call,
        client=client,
        sound_url=CONFIG.prompts.sounds.loading(),
    )  # Play loading sound a first time

    def _clear_tasks() -> None:
        chat_task.cancel()
        hard_timeout_task.cancel()
        loading_task.cancel()
        soft_timeout_task.cancel()

    is_error = True
    continue_chat = True
    try:
        while True:
            logger.debug(f"Chat task status: {chat_task.done()}")

            if pointer_current < float(
                (await _cache.aget(pointer_cache_key) or b"0").decode()
            ):  # Test if pointer updated by another instance
                logger.warning("Another chat is running, stopping this one")
                # Clean up Communication Services queue
                await handle_clear_queue(call=call, client=client)
                # Clean up tasks
                _clear_tasks()
                break

            if chat_task.done():  # Break when chat coroutine is done
                # Clean up
                _clear_tasks()
                # Get result
                is_error, continue_chat, call = (
                    chat_task.result()
                )  # Store updated chat model
                trainings_callback(call)  # Trigger trainings generation
                await _db.call_aset(
                    call
                )  # Save ASAP in DB allowing (1) user to cut off the Assistant and (2) SMS answers to be in order
                break

            if hard_timeout_task.done():  # Break when hard timeout is reached
                logger.warning(
                    f"Hard timeout of {CONFIG.conversation.answer_hard_timeout_sec}s reached"
                )
                # Clean up
                _clear_tasks()
                break

            if should_play_sound:  # Catch timeout if async loading is not started
                if (
                    soft_timeout_task.done() and not soft_timeout_triggered
                ):  # Speak when soft timeout is reached
                    logger.warning(
                        f"Soft timeout of {CONFIG.conversation.answer_soft_timeout_sec}s reached"
                    )
                    soft_timeout_triggered = True
                    await handle_recognize_text(
                        call=call,
                        client=client,
                        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
                        text=await CONFIG.prompts.tts.timeout_loading(call),
                        timeout_error=False,  # Voice will continue, don't trigger
                    )

                elif (
                    loading_task.done()
                ):  # Do not play timeout prompt plus loading, it can be frustrating for the user
                    loading_task = _loading_task()
                    await handle_media(
                        call=call,
                        client=client,
                        sound_url=CONFIG.prompts.sounds.loading(),
                    )  # Play loading sound

            # Wait to not block the event loop for other requests
            await asyncio.sleep(1)

    except Exception:
        logger.warning("Error loading intelligence", exc_info=True)

    if is_error:  # Error during chat
        if not continue_chat or _iterations_remaining < 1:  # Maximum retries reached
            logger.warning("Maximum retries reached, stopping chat")
            content = await CONFIG.prompts.tts.error(call)
            style = MessageStyleEnum.NONE
            await _tts_callback(content, style)
            call.messages.append(
                MessageModel(
                    content=content,
                    persona=MessagePersonaEnum.ASSISTANT,
                    style=style,
                )
            )

        else:  # Retry chat after an error
            logger.info(f"Retrying chat, {_iterations_remaining - 1} remaining")
            return await load_llm_chat(
                call=call,
                client=client,
                post_callback=post_callback,
                trainings_callback=trainings_callback,
                _iterations_remaining=_iterations_remaining - 1,
            )
    else:
        if continue_chat and _iterations_remaining > 0:  # Contiue chat
            logger.info(f"Continuing chat, {_iterations_remaining - 1} remaining")
            return await load_llm_chat(
                call=call,
                client=client,
                post_callback=post_callback,
                trainings_callback=trainings_callback,
                _iterations_remaining=_iterations_remaining - 1,
            )  # Recursive chat (like for for retry or tools)
        else:  # End chat
            await handle_recognize_text(
                call=call,
                client=client,
                style=MessageStyleEnum.NONE,
                text=None,
            )  # Trigger an empty text to recognize and generate timeout error if user does not speak

    return call


async def _execute_llm_chat(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], None],
    tts_callback: Callable[[str, MessageStyleEnum], Awaitable],
    use_tools: bool,
) -> Tuple[bool, bool, CallStateModel]:
    """
    Perform the chat with the LLM model.

    This function will handle:

    - The chat with the LLM model (incl system prompts, tools, and user callback)
    - Retry as possible if the LLM model fails to return a response

    Returns a tuple with:

    1. `bool`, notify error
    2. `bool`, should retry chat
    4. `CallStateModel`, the updated model
    """
    logger.debug("Running LLM chat")
    content_full = ""

    async def _buffer_callback(text: str, style: MessageStyleEnum) -> None:
        nonlocal content_full
        content_full += f" {text}"
        await tts_callback(text, style)

    async def _content_callback(
        buffer: str, style: MessageStyleEnum
    ) -> MessageStyleEnum:
        # Remove tool calls from buffer content and detect style
        local_style, local_content = extract_message_style(
            remove_message_action(buffer)
        )
        new_style = local_style or style
        if local_content:
            await tts_callback(local_content, new_style)
        return new_style

    # Build RAG
    trainings = await call.trainings()
    logger.info(f"Enhancing LLM chat with {len(trainings)} trainings")
    logger.debug(f"Trainings: {trainings}")

    # Build system prompts
    system = [
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.default_system(call),
            role="system",
        ),
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.chat_system(
                call=call,
                trainings=trainings,
            ),
            role="system",
        ),
    ]

    # Build plugins
    plugins = LlmPlugins(
        call=call,
        client=client,
        post_callback=post_callback,
        tts_callback=_buffer_callback,
    )

    tools = []
    if not use_tools:
        logger.warning("Tools disabled for this chat")
    else:
        tools = await plugins.to_openai(call)
        logger.debug(f"Tools: {tools}")

    # Execute LLM inference
    maximum_tokens_reached = False
    content_buffer_pointer = 0
    tool_calls_buffer: dict[int, MessageToolModel] = {}
    try:
        async for delta in completion_stream(
            max_tokens=133,  # Lowest possible value for 90% of the cases, if not sufficient, retry will be triggered, 100 tokens ~= 75 words, 20 words ~= 1 sentence, 5 sentences ~= 133 tokens
            messages=call.messages,
            system=system,
            tools=tools,
        ):
            if not delta.content:
                for piece in delta.tool_calls or []:
                    tool_calls_buffer[piece.index] = tool_calls_buffer.get(
                        piece.index, MessageToolModel()
                    )
                    tool_calls_buffer[piece.index] += piece
            else:
                # Store whole content
                content_full += delta.content
                for sentence in tts_sentence_split(
                    content_full[content_buffer_pointer:], False
                ):
                    content_buffer_pointer += len(sentence)
                    plugins.style = await _content_callback(sentence, plugins.style)
    except MaximumTokensReachedError:
        logger.warning("Maximum tokens reached for this completion, retry asked")
        maximum_tokens_reached = True
    except APIError as e:
        logger.warning(f"OpenAI API call error: {e}")
        return True, True, call  # Error, retry

    # Flush the remaining buffer
    if content_buffer_pointer < len(content_full):
        plugins.style = await _content_callback(
            content_full[content_buffer_pointer:], plugins.style
        )

    # Convert tool calls buffer
    tool_calls = [tool_call for _, tool_call in tool_calls_buffer.items()]

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    _, content_full = extract_message_style(remove_message_action(content_full))

    logger.debug(f"Chat response: {content_full}")
    logger.debug(f"Tool calls: {tool_calls}")

    # OpenAI GPT-4 Turbo sometimes return wrong tools schema, in that case, retry within limits
    # TODO: Tries to detect this error earlier
    # See: https://community.openai.com/t/model-tries-to-call-unknown-function-multi-tool-use-parallel/490653
    if any(
        tool_call.function_name == "multi_tool_use.parallel" for tool_call in tool_calls
    ):
        logger.warning(f'LLM send back invalid tool schema "multi_tool_use.parallel"')
        return True, True, call  # Error, retry

    # OpenAI GPT-4 Turbo tends to return empty content, in that case, retry within limits
    if not content_full and not tool_calls:
        logger.warning("Empty content, retrying")
        return True, True, call  # Error, retry

    # Execute tools
    tool_tasks = [tool_call.execute_function(plugins) for tool_call in tool_calls]
    await asyncio.gather(*tool_tasks)
    call = plugins.call  # Update call model if object reference changed

    # Store message
    if call.messages[-1].persona == MessagePersonaEnum.ASSISTANT:
        message = call.messages[-1]
        message.content = content_full.strip()
        message.style = plugins.style
        message.tool_calls = tool_calls
    else:
        message = MessageModel(
            content=content_full.strip(),
            persona=MessagePersonaEnum.ASSISTANT,
            style=plugins.style,
            tool_calls=tool_calls,
        )
        call.messages.append(message)

    if tool_calls:  # Recusive call if needed
        return False, True, call

    if maximum_tokens_reached:  # Retry if maximum tokens reached
        return False, True, call  # TODO: Should we notify an error?

    return False, False, call  # No error, no retry
