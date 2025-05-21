import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps

from aiojobs import Scheduler
from azure.cognitiveservices.speech import (
    SpeechSynthesizer,
)
from azure.communication.callautomation.aio import CallAutomationClient

from app.helpers.call_utils import (
    AECStream,
    SttClient,
    handle_media,
    handle_realtime_tts,
    tts_sentence_split,
    use_tts_client,
)
from app.helpers.config import CONFIG
from app.helpers.features import (
    answer_hard_timeout_sec,
    answer_soft_timeout_sec,
    phone_silence_timeout_sec,
    vad_cutoff_timeout_ms,
    vad_silence_timeout_ms,
)
from app.helpers.llm_tools import DefaultPlugin
from app.helpers.llm_worker import (
    MaximumTokensReachedError,
    SafetyCheckError,
    completion_stream,
)
from app.helpers.logging import logger
from app.helpers.monitoring import (
    SpanAttributeEnum,
    call_cutoff_latency,
    gauge_set,
    start_as_current_span,
)
from app.models.call import CallStateModel
from app.models.message import (
    ActionEnum as MessageAction,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
    ToolModel as MessageToolModel,
    extract_message_style,
)

_db = CONFIG.database.instance


# TODO: Refacto, this function is too long
@start_as_current_span("call_load_llm_chat")
async def load_llm_chat(  # noqa: PLR0913
    audio_in: asyncio.Queue[bytes],
    audio_out: asyncio.Queue[bytes | bool],
    audio_sample_rate: int,
    automation_client: CallAutomationClient,
    call: CallStateModel,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
    training_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    # Init language recognition
    audio_tts: asyncio.Queue[bytes] = asyncio.Queue()

    async with (
        SttClient(
            call=call,
            sample_rate=audio_sample_rate,
            scheduler=scheduler,
        ) as stt_client,
        use_tts_client(
            call=call,
            out=audio_tts,
        ) as tts_client,
        AECStream(
            in_raw_queue=audio_in,
            in_reference_queue=audio_tts,
            out_queue=audio_out,
            sample_rate=audio_sample_rate,
            scheduler=scheduler,
        ) as aec,
    ):
        # Build scheduler
        last_chat: asyncio.Task | None = None

        async def _timeout_callback() -> None:
            """
            Triggered when the phone silence timeout is reached.
            """
            from app.helpers.call_events import on_realtime_recognize_error

            logger.info("Phone silence timeout triggered")

            # Execute business logic
            await scheduler.spawn(
                on_realtime_recognize_error(
                    call=call,
                    client=automation_client,
                    post_callback=post_callback,
                    scheduler=scheduler,
                    tts_client=tts_client,
                )
            )

        async def _stop_callback() -> None:
            """
            Triggered when the audio buffer needs to be cleared.
            """
            # Report the cutoff latency
            start = time.monotonic()

            # Cancel previous chat
            if last_chat:
                last_chat.cancel()

            # Stop TTS task
            tts_client.stop_speaking_async()

            # Clear the out buffer
            while not audio_out.empty():
                audio_out.get_nowait()
                audio_out.task_done()

            # Send a stop signal
            await audio_out.put(False)

            # Report the cutoff latency
            gauge_set(
                metric=call_cutoff_latency,
                value=time.monotonic() - start,
            )

        async def _commit_answer(
            wait: bool,
            tool_blacklist: set[str] = set(),
        ) -> None:
            """
            Process the response.

            Start the chat task and wait for its response if needed. Job is stored in `last_response` shared variable.
            """
            # Start chat task
            nonlocal last_chat
            last_chat = asyncio.create_task(
                _continue_chat(
                    call=call,
                    client=automation_client,
                    post_callback=post_callback,
                    scheduler=scheduler,
                    tool_blacklist=tool_blacklist,
                    training_callback=training_callback,
                    tts_client=tts_client,
                )
            )

            # Wait for its response
            if wait:
                await last_chat

        async def _response_callback(_retry: bool = False) -> None:
            """
            Triggered when the audio buffer needs to be processed.

            If the recognition is empty, retry the recognition once. Otherwise, process the response.
            """
            # Report the answer latency
            aec.answer_start()

            # Pull the recognition
            stt_text = await stt_client.pull_recognition()

            # Ignore empty recognition
            if not stt_text:
                # Skip if already retries
                if _retry:
                    return
                # Retry recognition, maybe the user was too fast or the recognition is temporarly slow
                await asyncio.sleep(0.2)
                return await _response_callback(_retry=True)

            # Stop any previous response, but keep the metrics
            await _stop_callback()

            # Add it to the call history and update last interaction
            logger.info("Voice stored: %s", stt_text)
            async with _db.call_transac(
                call=call,
                scheduler=scheduler,
            ):
                call.last_interaction_at = datetime.now(UTC)
                call.messages.append(
                    MessageModel(
                        content=stt_text,
                        lang_short_code=call.lang.short_code,
                        persona=MessagePersonaEnum.HUMAN,
                    )
                )

            # Process the response and wait for it to be able to kill the task if needed
            await _commit_answer(wait=True)

        # First call
        if len(call.messages) <= 1:
            # Welcome with a pre-recorded message
            await handle_realtime_tts(
                call=call,
                tts_client=tts_client,
                scheduler=scheduler,
                text=await CONFIG.prompts.tts.hello(call),
            )
        # User is back
        else:
            # Welcome with the LLM, do not use the end call tool for the first message, LLM hallucinates it and this is extremely frustrating for the user, don't wait for the response to start the VAD quickly
            await _commit_answer(
                tool_blacklist={"end_call"},
                wait=False,
            )

        # Detect VAD
        await _process_audio_for_vad(
            call=call,
            in_callback=aec.pull_audio,
            out_callback=stt_client.push_audio,
            response_callback=_response_callback,
            stop_callback=_stop_callback,
            timeout_callback=_timeout_callback,
        )


# TODO: Refacto, this function is too long (and remove PLR0912/PLR0915 ignore)
@start_as_current_span("call_continue_chat")
async def _continue_chat(  # noqa: PLR0915, PLR0913
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
    training_callback: Callable[[CallStateModel], Awaitable[None]],
    tts_client: SpeechSynthesizer,
    tool_blacklist: set[str] = set(),
    _iterations_remaining: int = 3,
) -> CallStateModel:
    """
    Handle the intelligence of the call, including: LLM chat, TTS, and media play.

    Play the loading sound while waiting for the intelligence to be processed. If the intelligence is not processed after few secs, play the timeout sound. If the intelligence is not processed after more secs, stop the intelligence processing and play the error sound.

    Returns the updated call model.
    """
    # Add span attributes
    SpanAttributeEnum.CALL_CHANNEL.attribute("voice")
    SpanAttributeEnum.CALL_MESSAGE.attribute(call.messages[-1].content)

    # Reset recognition retry counter
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.recognition_retry = 0

    # By default, play the loading sound
    play_loading_sound = True

    async def _tts_callback(text: str, style: MessageStyleEnum) -> None:
        """
        Send back the TTS to the user.
        """
        nonlocal play_loading_sound
        # For first TTS, interrupt loading sound and disable loading it
        if play_loading_sound:
            play_loading_sound = False
        # Play the TTS
        await handle_realtime_tts(
            call=call,
            scheduler=scheduler,
            style=style,
            text=text,
            tts_client=tts_client,
        )

    # Chat
    chat_task = asyncio.create_task(
        _generate_chat_completion(
            call=call,
            client=client,
            post_callback=post_callback,
            scheduler=scheduler,
            tool_blacklist=tool_blacklist,
            tts_callback=_tts_callback,
            tts_client=tts_client,
            use_tools=_iterations_remaining > 0,
        )
    )

    # Loading
    def _loading_task() -> asyncio.Task:
        return asyncio.create_task(asyncio.sleep(loading_timer))

    loading_timer = 5  # Play loading sound every 5 secs
    loading_task = _loading_task()

    # Timeouts
    soft_timeout_triggered = False
    soft_timeout_task = asyncio.create_task(
        asyncio.sleep(await answer_soft_timeout_sec())
    )
    hard_timeout_task = asyncio.create_task(
        asyncio.sleep(await answer_hard_timeout_sec())
    )

    def _clear_tasks() -> None:
        chat_task.cancel()
        hard_timeout_task.cancel()
        loading_task.cancel()
        soft_timeout_task.cancel()

    is_error = True
    continue_chat = True
    try:
        while True:
            # logger.debug("Chat task status: %s", chat_task.done())

            # Break when chat coroutine is done
            if chat_task.done():
                # Clean up
                _clear_tasks()
                # Get result
                is_error, continue_chat, call = (
                    chat_task.result()
                )  # Store updated chat model
                await training_callback(call)  # Trigger trainings generation
                break

            # Break when hard timeout is reached
            if hard_timeout_task.done():
                logger.warning(
                    "Hard timeout of %ss reached",
                    await answer_hard_timeout_sec(),
                )
                # Clean up
                _clear_tasks()
                break

            # Catch timeout if async loading is not started
            if play_loading_sound:
                # Speak when soft timeout is reached
                if soft_timeout_task.done() and not soft_timeout_triggered:
                    logger.warning(
                        "Soft timeout of %ss reached",
                        await answer_soft_timeout_sec(),
                    )
                    soft_timeout_triggered = True
                    # Never store the error message in the call history, it has caused hallucinations in the LLM
                    await handle_realtime_tts(
                        call=call,
                        scheduler=scheduler,
                        store=False,
                        text=await CONFIG.prompts.tts.timeout_loading(call),
                        tts_client=tts_client,
                    )

                # Do not play timeout prompt plus loading, it can be frustrating for the user
                elif loading_task.done():
                    loading_task = _loading_task()
                    await scheduler.spawn(
                        handle_media(
                            call=call,
                            client=client,
                            sound_url=CONFIG.prompts.sounds.loading(),
                        )
                    )

            # Wait to not block the event loop for other requests
            await asyncio.sleep(1)

    except Exception:
        # TODO: Remove last message
        logger.exception("Error loading intelligence")

    # Error during chat
    if is_error:
        # Maximum retries reached
        if not continue_chat or _iterations_remaining < 1:
            logger.warning("Maximum retries reached, stopping chat")
            content = await CONFIG.prompts.tts.error(call)
            # Speak the error
            await _tts_callback(content, MessageStyleEnum.NONE)
            # Never store the error message in the call history, it has caused hallucinations in the LLM

        # Retry chat after an error
        else:
            logger.info("Retrying chat, %s remaining", _iterations_remaining - 1)
            return await _continue_chat(
                call=call,
                client=client,
                post_callback=post_callback,
                scheduler=scheduler,
                tool_blacklist=tool_blacklist,
                training_callback=training_callback,
                tts_client=tts_client,
                _iterations_remaining=_iterations_remaining - 1,
            )

    # Contiue chat
    elif continue_chat and _iterations_remaining > 0:
        logger.info("Continuing chat, %s remaining", _iterations_remaining - 1)
        return await _continue_chat(
            call=call,
            client=client,
            post_callback=post_callback,
            scheduler=scheduler,
            tool_blacklist=tool_blacklist,
            training_callback=training_callback,
            tts_client=tts_client,
            _iterations_remaining=_iterations_remaining - 1,
        )  # Recursive chat (like for for retry or tools)

        # End chat
        # TODO: Re-implement

    return call


# TODO: Refacto, this function is too long
@start_as_current_span("call_generate_chat_completion")
async def _generate_chat_completion(  # noqa: PLR0913, PLR0912, PLR0915
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
    tool_blacklist: set[str],
    tts_callback: Callable[[str, MessageStyleEnum], Awaitable[None]],
    tts_client: SpeechSynthesizer,
    use_tools: bool,
) -> tuple[bool, bool, CallStateModel]:
    """
    Perform the chat with the LLM model.

    This function will handle:

    - The chat with the LLM model (incl system prompts, tools, and user callback)
    - Retry as possible if the LLM model fails to return a response

    Returns a tuple with:

    1. `bool`, notify error
    2. `bool`, should retry chat
    3. `CallStateModel`, the updated model
    """
    logger.debug("Running LLM chat")
    content_full = ""

    async def _plugin_tts_callback(text: str) -> None:
        nonlocal content_full
        content_full += f" {text}"
        await tts_callback(text, MessageStyleEnum.NONE)

    async def _content_callback(buffer: str) -> None:
        # Remove tool calls from buffer content and detect style
        style, local_content = extract_message_style(buffer)
        await tts_callback(local_content, style)

    # Build RAG
    trainings = await call.trainings()
    logger.info("Enhancing LLM chat with %s trainings", len(trainings))
    # logger.debug("Trainings: %s", trainings)

    # System prompts
    system = CONFIG.prompts.llm.chat_system(
        call=call,
        trainings=trainings,
    )

    # Build plugins
    plugins = DefaultPlugin(
        call=call,
        client=client,
        post_callback=post_callback,
        scheduler=scheduler,
        tts_callback=_plugin_tts_callback,
        tts_client=tts_client,
    )

    tools = []
    if not use_tools:
        logger.warning("Tools disabled for this chat")
    else:
        tools = await plugins.to_openai(frozenset(tool_blacklist))
        # logger.debug("Tools: %s", tools)

    # Translate messages to avoid LLM hallucinations
    # See: https://github.com/microsoft/call-center-ai/issues/260
    translated_messages = await asyncio.gather(
        *[message.translate(call.lang.short_code) for message in call.messages]
    )
    # logger.debug("Translated messages: %s", translated_messages)

    # Execute LLM inference
    content_buffer_pointer = 0
    last_buffered_tool_id = None
    maximum_tokens_reached = False
    tool_calls_buffer: dict[str, MessageToolModel] = {}
    try:
        # Consume the completion stream
        async for delta in completion_stream(
            max_tokens=160,  # Lowest possible value for 90% of the cases, if not sufficient, retry will be triggered, 100 tokens ~= 75 words, 20 words ~= 1 sentence, 6 sentences ~= 160 tokens
            messages=translated_messages,
            system=system,
            tools=tools,
        ):
            # Complete tools
            if delta.tool_calls:
                for piece in delta.tool_calls:
                    # Azure AI Inference sometimes returns empty tool IDs, in that case, use the last one
                    if piece.id:
                        last_buffered_tool_id = piece.id
                    # No tool ID, alert and skip
                    if not last_buffered_tool_id:
                        logger.warning(
                            "Empty tool ID, cannot buffer tool call: %s", piece
                        )
                        continue
                    # New, init buffer
                    if last_buffered_tool_id not in tool_calls_buffer:
                        tool_calls_buffer[last_buffered_tool_id] = MessageToolModel()
                    # Append
                    tool_calls_buffer[last_buffered_tool_id].add_delta(piece)

            # Complete content
            if delta.content:
                content_full += delta.content
                for sentence, length in tts_sentence_split(
                    content_full[content_buffer_pointer:], False
                ):
                    content_buffer_pointer += length
                    await _content_callback(sentence)

    # Retry on maximum tokens reached
    except MaximumTokensReachedError:
        logger.warning("Maximum tokens reached for this completion, retry asked")
        maximum_tokens_reached = True
    # Last user message is trash, remove it
    except SafetyCheckError as e:
        logger.warning("Safety Check error: %s", e)
        # Remove last user message
        if last_message := next(
            (
                call
                for call in reversed(call.messages)
                if call.persona == MessagePersonaEnum.HUMAN
                and call.action in [MessageAction.SMS, MessageAction.TALK]
            ),
            None,
        ):
            call.messages.remove(last_message)
        return True, False, call  # Error, no retry

    # Flush the remaining buffer
    if content_buffer_pointer < len(content_full):
        await _content_callback(content_full[content_buffer_pointer:])

    # Convert tool calls buffer
    tool_calls = [tool_call for _, tool_call in tool_calls_buffer.items()]

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    last_style, content_full = extract_message_style(content_full)

    logger.debug("Completion response: %s", content_full)
    logger.debug("Completion tools: %s", tool_calls)

    # OpenAI GPT-4 Turbo sometimes return wrong tools schema, in that case, retry within limits
    # TODO: Tries to detect this error earlier
    # See: https://community.openai.com/t/model-tries-to-call-unknown-function-multi-tool-use-parallel/490653
    if any(
        tool_call.function_name == "multi_tool_use.parallel" for tool_call in tool_calls
    ):
        logger.warning('LLM send back invalid tool schema "multi_tool_use.parallel"')
        return True, True, call  # Error, retry

    # OpenAI GPT-4 Turbo tends to return empty content, in that case, retry within limits
    if not content_full and not tool_calls:
        logger.warning("Empty content, retrying")
        return True, True, call  # Error, retry

    # Execute tools
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        await asyncio.gather(
            *[
                plugins.execute(
                    blacklist=tool_blacklist,
                    tool=tool_call,
                )
                for tool_call in tool_calls
            ]
        )

    # Update call model if object reference changed
    call = plugins.call

    # Store message
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.messages.append(
            MessageModel(
                content="",  # Content has already been stored within the TTS callback
                persona=MessagePersonaEnum.ASSISTANT,
                style=last_style,
                tool_calls=tool_calls,
            )
        )

    # Recusive call if needed
    if tool_calls:
        return False, True, call
    # Retry if maximum tokens reached
    if maximum_tokens_reached:
        return False, True, call  # TODO: Should we notify an error?
    # No error, no retry
    return False, False, call


# TODO: Refacto and simplify
async def _process_audio_for_vad(  # noqa: PLR0913
    call: CallStateModel,
    in_callback: Callable[[], Awaitable[tuple[bytes, bool]]],
    out_callback: Callable[[bytes], None],
    response_callback: Callable[[], Awaitable[None]],
    stop_callback: Callable[[], Awaitable[None]],
    timeout_callback: Callable[[], Awaitable[None]],
) -> None:
    """
    Process voice activity and silence detection.

    Follows the following steps:

    - Detect voice activity and clear the TTS to let the user speak
    - Wait for silence and trigger the chat
    - Wait for longer silence and trigger the timeout
    """
    stop_task: asyncio.Task | None = None
    silence_task: asyncio.Task | None = None

    async def _wait_for_silence() -> None:
        """
        Run the chat after a silence.

        If the silence is too long, run the timeout.
        """
        # Wait before flushing
        nonlocal stop_task
        timeout_ms = await vad_silence_timeout_ms()
        await asyncio.sleep(timeout_ms / 1000)

        # Cancel the clear TTS task
        if stop_task:
            stop_task.cancel()
            stop_task = None

        # Flush the audio buffer
        logger.debug("Flushing audio buffer after %i ms", timeout_ms)
        await response_callback()

        # Wait for silence and trigger timeout
        timeout_sec = await phone_silence_timeout_sec()
        while True:
            # Stop this time if the call played a message
            timeout_start = datetime.now(UTC)
            await asyncio.sleep(timeout_sec)

            # Stop if the call ended
            if not call.in_progress:
                break

            # Cancel if an interaction happened in the meantime
            if (
                call.last_interaction_at
                and call.last_interaction_at + timedelta(seconds=timeout_sec)
                > timeout_start
            ):
                logger.debug(
                    "Message sent in the meantime, canceling this silence timeout"
                )
                continue

            # Trigger the timeout
            logger.info("Silence triggered after %i sec", timeout_sec)
            await timeout_callback()

    async def _wait_for_stop() -> None:
        """
        Stop the TTS if user speaks for too long.
        """
        timeout_ms = await vad_cutoff_timeout_ms()

        # Wait before clearing the TTS queue
        await asyncio.sleep(timeout_ms / 1000)

        # Clear the queue
        logger.info("Stoping TTS after %i ms", timeout_ms)
        await stop_callback()

    while True:
        # Wait for the next audio packet
        out_chunck, is_speech = await in_callback()

        # Add audio to the buffer
        out_callback(out_chunck)

        # If no speech, init the silence task
        if not is_speech:
            # Start timeout if not already started
            if not silence_task:
                silence_task = asyncio.create_task(_wait_for_silence())
            # Continue to the next audio packet
            continue

        # Voice detected, cancel the timeout task
        if silence_task:
            silence_task.cancel()
            silence_task = None

        # Start the TTS clear task
        if not stop_task:
            stop_task = asyncio.create_task(_wait_for_stop())


def _tts_callback(
    call: CallStateModel,
    scheduler: Scheduler,
    tts_client: SpeechSynthesizer,
) -> Callable[[str, MessageStyleEnum], Awaitable[None]]:
    """
    Send back the TTS to the user.
    """

    @wraps(_tts_callback)
    async def wrapper(
        text: str,
        style: MessageStyleEnum,
    ) -> None:
        # Skip if no text
        if not text:
            return

        # Play the TTS
        await handle_realtime_tts(
            call=call,
            scheduler=scheduler,
            style=style,
            text=text,
            tts_client=tts_client,
        )

    return wrapper
