from typing import Awaitable, Callable, List, Optional, Tuple, Type
from azure.communication.callautomation import (
    CallConnectionClient,
)
from helpers.config import CONFIG
from helpers.logging import build_logger
from models.call import CallModel
from models.message import (
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
    ToolModel as MessageToolModel,
    extract_message_style,
    remove_message_action,
)
from helpers.call_utils import (
    handle_media,
    handle_play,
    handle_recognize_text,
    tts_sentence_split,
)
from httpx import ReadError
from fastapi import BackgroundTasks
from helpers.llm_tools import LlmPlugins
import asyncio
from helpers.llm_worker import (
    completion_model_sync,
    completion_stream,
    completion_sync,
    ModelType,
    safety_check,
    SafetyCheckError,
)
from openai import APIError
from openai.types.chat import ChatCompletionSystemMessageParam


_logger = build_logger(__name__)
_db = CONFIG.database.instance()
_search = CONFIG.ai_search.instance()


async def llm_completion(text: Optional[str], call: CallModel) -> Optional[str]:
    """
    Run LLM completion from a system prompt and a Call model.

    If the system prompt is None, no completion will be run and None will be returned. Otherwise, the response of the LLM will be returned.
    """
    _logger.info("Running LLM completion")

    if not text:
        return None

    system = _llm_completion_system(text, call)
    content = None

    try:
        content = await completion_sync(
            max_tokens=1000,
            messages=call.messages,
            system=system,
        )
    except ReadError:
        _logger.warn("Network error", exc_info=True)
    except APIError as e:
        _logger.warn(f"OpenAI API call error: {e}")
    except SafetyCheckError as e:
        _logger.warn(f"OpenAI safety check error: {e}")

    return content


async def llm_model(
    text: Optional[str], call: CallModel, model: Type[ModelType]
) -> Optional[ModelType]:
    """
    Run LLM completion from a system prompt, a Call model, and an expected model type as a return.

    The logic will try its best to return a model of the expected type, but it is not guaranteed. It it fails, `None` will be returned.
    """
    _logger.debug("Running LLM model")

    if not text:
        return None

    system = _llm_completion_system(text, call)
    res = None

    try:
        res = await completion_model_sync(
            max_tokens=1000,
            messages=call.messages,
            model=model,
            system=system,
        )
    except ReadError:
        _logger.warn("Network error", exc_info=True)
    except APIError as e:
        _logger.warn(f"OpenAI API call error: {e}")

    return res


def _llm_completion_system(
    system: str, call: CallModel
) -> List[ChatCompletionSystemMessageParam]:
    messages = [
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.default_system(
                phone_number=call.phone_number,
            ),
            role="system",
        ),
        ChatCompletionSystemMessageParam(
            content=system,
            role="system",
        ),
    ]
    _logger.debug(f"Messages: {messages}")
    return messages


async def load_llm_chat(
    background_tasks: BackgroundTasks,
    call: CallModel,
    client: CallConnectionClient,
    post_call_intelligence: Callable[[CallModel, BackgroundTasks], None],
    _backup_model: bool = False,
    _iterations_remaining: int = 3,
) -> CallModel:
    """
    Handle the intelligence of the call, including: LLM chat, TTS, and media play.

    Play the loading sound while waiting for the intelligence to be processed. If the intelligence is not processed after few seconds, play the timeout sound. If the intelligence is not processed after more seconds, stop the intelligence processing and play the error sound.

    Returns the updated call model.
    """
    _logger.info("Loading LLM chat")

    should_play_sound = True

    async def _user_callback(text: str, style: MessageStyleEnum) -> None:
        """
        Send back the TTS to the user.
        """
        nonlocal should_play_sound

        try:
            await safety_check(text)
        except SafetyCheckError as e:
            _logger.warn(f"Unsafe text detected, not playing: {e}")
            return

        should_play_sound = False
        await handle_play(
            call=call,
            client=client,
            store=False,
            style=style,
            text=text,
        )

    if _backup_model:
        _logger.warn("Using backup model")

    chat_task = asyncio.create_task(
        _execute_llm_chat(
            background_tasks=background_tasks,
            backup_model=_backup_model,
            call=call,
            client=client,
            post_call_intelligence=post_call_intelligence,
            use_tools=_iterations_remaining > 0,
            user_callback=_user_callback,
        )
    )

    soft_timeout_triggered = False
    soft_timeout_task = asyncio.create_task(
        asyncio.sleep(CONFIG.workflow.intelligence_soft_timeout_sec)
    )
    hard_timeout_task = asyncio.create_task(
        asyncio.sleep(CONFIG.workflow.intelligence_hard_timeout_sec)
    )

    is_error = True
    continue_chat = True
    should_user_answer = True
    try:
        while True:
            _logger.debug(f"Chat task status: {chat_task.done()}")
            if chat_task.done():  # Break when chat coroutine is done
                # Clean up
                soft_timeout_task.cancel()
                hard_timeout_task.cancel()
                # Store updated chat model
                is_error, continue_chat, should_user_answer, call = chat_task.result()
                # Save in DB for new claims and allowing demos to be more "real-time"
                await _db.call_aset(call)
                break

            if hard_timeout_task.done():  # Break when hard timeout is reached
                _logger.warn(
                    f"Hard timeout of {CONFIG.workflow.intelligence_hard_timeout_sec}s reached"
                )
                # Clean up
                chat_task.cancel()
                soft_timeout_task.cancel()
                break

            if should_play_sound:  # Catch timeout if async loading is not started
                if (
                    soft_timeout_task.done() and not soft_timeout_triggered
                ):  # Speak when soft timeout is reached
                    _logger.warn(
                        f"Soft timeout of {CONFIG.workflow.intelligence_soft_timeout_sec}s reached"
                    )
                    soft_timeout_triggered = True
                    await handle_play(
                        call=call,
                        client=client,
                        text=await CONFIG.prompts.tts.timeout_loading(call),
                        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
                    )

                else:  # Do not play timeout prompt plus loading, it can be frustrating for the user
                    await handle_media(
                        call=call,
                        client=client,
                        sound_url=CONFIG.prompts.sounds.loading(),
                    )  # Play loading sound

            # Wait to not block the event loop and play too many sounds
            await asyncio.sleep(5)

    except Exception:
        _logger.warn("Error loading intelligence", exc_info=True)

    if is_error:  # Error during chat
        if not continue_chat or _iterations_remaining < 1:  # Maximum retries reached
            _logger.warn("Maximum retries reached, stopping chat")
            should_user_answer = True
            content = await CONFIG.prompts.tts.error(call)
            style = MessageStyleEnum.NONE
            await _user_callback(content, style)
            call.messages.append(
                MessageModel(
                    content=content,
                    persona=MessagePersonaEnum.ASSISTANT,
                    style=style,
                )
            )

        else:  # Retry chat after an error
            _logger.info(f"Retrying chat, {_iterations_remaining - 1} remaining")
            return await load_llm_chat(
                background_tasks=background_tasks,
                call=call,
                client=client,
                post_call_intelligence=post_call_intelligence,
                _backup_model=(
                    _iterations_remaining < 2
                ),  # Enable backup model if two retries are left, to maximize the chance of success
                _iterations_remaining=_iterations_remaining - 1,
            )

    elif continue_chat:  # Contiue chat
        _logger.info(f"Continuing chat, {_iterations_remaining - 1} remaining")
        return await load_llm_chat(
            background_tasks=background_tasks,
            call=call,
            client=client,
            post_call_intelligence=post_call_intelligence,
            _backup_model=_backup_model,
            _iterations_remaining=_iterations_remaining - 1,
        )

    if should_user_answer:
        await handle_recognize_text(
            call=call,
            client=client,
        )

    return call


async def _execute_llm_chat(
    background_tasks: BackgroundTasks,
    backup_model: bool,
    call: CallModel,
    client: CallConnectionClient,
    post_call_intelligence: Callable[[CallModel, BackgroundTasks], None],
    use_tools: bool,
    user_callback: Callable[[str, MessageStyleEnum], Awaitable],
) -> Tuple[bool, bool, bool, CallModel]:
    """
    Perform the chat with the LLM model.

    This function will handle:

    - The chat with the LLM model (incl system prompts, tools, and user callback)
    - Retry as possible if the LLM model fails to return a response

    Returns a tuple with:

    1. `bool`, notify error
    2. `bool`, should retry chat
    3. `bool`, if the chat should continue
    4. `CallModel`, the updated model
    """
    _logger.debug("Running LLM chat")
    content_full = ""
    should_user_answer = True

    async def _tools_callback(text: str, style: MessageStyleEnum) -> None:
        nonlocal content_full
        content_full += f" {text}"
        await user_callback(text, style)

    async def _content_callback(
        buffer: str, style: MessageStyleEnum
    ) -> MessageStyleEnum:
        # Remove tool calls from buffer content and detect style
        local_style, local_content = extract_message_style(
            remove_message_action(buffer)
        )
        new_style = local_style or style
        if local_content:
            await user_callback(local_content, new_style)
        return new_style

    async def _tools_cancellation_callback() -> None:
        nonlocal should_user_answer
        _logger.info("Chat stopped by tool")
        should_user_answer = False

    # Build RAG using query expansion from last messages
    trainings_tasks = await asyncio.gather(
        *[
            _search.training_asearch_all(message.content, call)
            for message in call.messages[-CONFIG.ai_search.expansion_k :]
        ],
    )
    trainings = sorted(
        set(training for trainings in trainings_tasks for training in trainings or [])
    )  # Flatten, remove duplicates, and sort by score
    _logger.info(f"Enhancing LLM chat with {len(trainings)} trainings")
    _logger.debug(f"Trainings: {trainings}")

    # Build system prompts
    system = [
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.default_system(
                phone_number=call.phone_number,
            ),
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
        cancellation_callback=_tools_cancellation_callback,
        client=client,
        post_call_intelligence=lambda call: post_call_intelligence(
            call, background_tasks
        ),
        user_callback=_tools_callback,
    )

    tools = []
    if not use_tools:
        _logger.warn("Tools disabled for this chat")
    else:
        tools = plugins.to_openai()
        _logger.debug(f"Tools: {tools}")

    # Execute LLM inference
    content_buffer_pointer = 0
    tool_calls_buffer: dict[int, MessageToolModel] = {}
    try:
        async for delta in completion_stream(
            is_backup=backup_model,
            max_tokens=350,
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
    except ReadError:
        _logger.warn("Network error", exc_info=True)
        return True, True, should_user_answer, call
    except APIError as e:
        _logger.warn(f"OpenAI API call error: {e}")
        return True, True, should_user_answer, call

    # Flush the remaining buffer
    if content_buffer_pointer < len(content_full):
        plugins.style = await _content_callback(
            content_full[content_buffer_pointer:], plugins.style
        )

    # Convert tool calls buffer
    tool_calls = [tool_call for _, tool_call in tool_calls_buffer.items()]

    # Get data from full content to be able to store it in the DB
    _, content_full = extract_message_style(remove_message_action(content_full))

    _logger.debug(f"Chat response: {content_full}")
    _logger.debug(f"Tool calls: {tool_calls}")

    # OpenAI GPT-4 Turbo sometimes return wrong tools schema, in that case, retry within limits
    # TODO: Tries to detect this error earlier
    # See: https://community.openai.com/t/model-tries-to-call-unknown-function-multi-tool-use-parallel/490653
    if any(
        tool_call.function_name == "multi_tool_use.parallel" for tool_call in tool_calls
    ):
        _logger.warn(f'LLM send back invalid tool schema "multi_tool_use.parallel"')
        return True, True, should_user_answer, call

    # OpenAI GPT-4 Turbo tends to return empty content, in that case, retry within limits
    if not content_full and not tool_calls:
        _logger.warn("Empty content, retrying")
        return True, True, should_user_answer, call

    # Execute tools
    tool_tasks = [tool_call.execute_function(plugins) for tool_call in tool_calls]
    await asyncio.gather(*tool_tasks)

    # Store message
    call.messages.append(
        MessageModel(
            content=content_full.strip(),
            persona=MessagePersonaEnum.ASSISTANT,
            style=plugins.style,
            tool_calls=tool_calls,
        )
    )

    # Recusive call if needed
    if tool_calls and should_user_answer:
        return False, True, should_user_answer, call

    return False, False, should_user_answer, call
