import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps

import aiojobs
from azure.cognitiveservices.speech import (
    AudioConfig,
    SpeechConfig,
    SpeechRecognitionEventArgs,
    SpeechRecognizer,
)
from azure.cognitiveservices.speech.audio import AudioStreamFormat, PushAudioInputStream
from azure.communication.callautomation.aio import CallAutomationClient
from openai import APIError
from pydub import AudioSegment
from pydub.effects import high_pass_filter, low_pass_filter

from app.helpers.call_utils import (
    handle_clear_queue,
    handle_media,
    handle_play_text,
    tts_sentence_split,
)
from app.helpers.config import CONFIG
from app.helpers.features import (
    answer_hard_timeout_sec,
    answer_soft_timeout_sec,
    vad_cutoff_timeout_ms,
    vad_silence_timeout_ms,
    vad_threshold,
)
from app.helpers.identity import token
from app.helpers.llm_tools import DefaultPlugin
from app.helpers.llm_worker import (
    MaximumTokensReachedError,
    SafetyCheckError,
    completion_stream,
)
from app.helpers.logging import logger
from app.helpers.monitoring import CallAttributes, span_attribute, tracer
from app.models.call import CallStateModel
from app.models.message import (
    ActionEnum as MessageAction,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
    ToolModel as MessageToolModel,
    extract_message_style,
    remove_message_action,
)

_db = CONFIG.database.instance()


# TODO: Refacto, this function is too long
@tracer.start_as_current_span("call_load_llm_chat")
async def load_llm_chat(  # noqa: PLR0913
    audio_bits_per_sample: int,
    audio_channels: int,
    audio_sample_rate: int,
    audio_stream: asyncio.Queue[bytes],
    automation_client: CallAutomationClient,
    call: CallStateModel,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    training_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    # Init language recognition
    speech_token = await (await token("https://cognitiveservices.azure.com/.default"))()
    recognizer_buffer: list[str] = []
    recognizer_store_next_recognition = False
    recognizer_lock = asyncio.Event()
    recognizer_stream = PushAudioInputStream(
        stream_format=AudioStreamFormat(
            bits_per_sample=audio_bits_per_sample,
            channels=audio_channels,
            samples_per_second=audio_sample_rate,
        ),
    )
    recognizer_config = SpeechConfig(
        auth_token=f"aad#{CONFIG.cognitive_service.resource_id}#{speech_token}",
        region=CONFIG.cognitive_service.region,
    )
    # recognizer_config.set_property(PropertyId.Speech_LogFilename, f"speech-{uuid4()}.log")
    recognizer_client = SpeechRecognizer(
        audio_config=AudioConfig(stream=recognizer_stream),
        language=call.lang.short_code,
        speech_config=recognizer_config,
    )

    def _handle_partial_recognition(event: SpeechRecognitionEventArgs) -> None:
        text = event.result.text

        # Skip if no text
        if not text:
            return

        # Init buffer if empty
        if not recognizer_buffer:
            recognizer_buffer.append("")

        # Replace last element by this update
        recognizer_buffer[-1] = text
        logger.debug("Partial recognition: %s", recognizer_buffer)

        # Lock the recognition until the audio stream is ready
        recognizer_lock.set()

    def _handle_complete_recognition(event: SpeechRecognitionEventArgs) -> None:
        text = event.result.text

        # Skip if no text
        if not text:
            return

        # Replace last element by this update
        recognizer_buffer[-1] = text

        # If recognition requires to be stored, add it to the call history
        nonlocal recognizer_store_next_recognition
        if recognizer_store_next_recognition:
            recognizer_store_next_recognition = False
            logger.info("Voice stored: %s", recognizer_buffer)
            call.messages.append(
                MessageModel(
                    content=" ".join(recognizer_buffer),
                    persona=MessagePersonaEnum.HUMAN,
                )
            )

        # Add a new element to the buffer, thus the next partial recognition will be in a new element
        recognizer_buffer.append("")
        logger.debug("Complete recognition: %s", recognizer_buffer)

    # Register callback and start recognition
    recognizer_client.recognizing.connect(_handle_partial_recognition)
    recognizer_client.recognized.connect(_handle_complete_recognition)
    recognizer_client.session_started.connect(
        lambda _: logger.debug("Recognition started")
    )
    recognizer_client.session_stopped.connect(
        lambda _: logger.debug("Recognition stopped")
    )
    recognizer_client.canceled.connect(
        lambda event: logger.warning("Recognition cancelled: %s", event)
    )
    recognizer_client.start_continuous_recognition_async()

    # Build scheduler
    last_response: aiojobs.Job | None = None
    async with aiojobs.Scheduler() as scheduler:

        async def _clear_audio_callback() -> None:
            # Wait for the recognition to be ready
            await recognizer_lock.wait()

            # Clear the LLM queue
            recognizer_buffer.clear()

            # Clear the TTS queue
            await scheduler.spawn(
                handle_clear_queue(
                    call=call,
                    client=automation_client,
                )
            )

            # Cancel the last response
            if last_response:
                # Wait 2 secs maximum for the task to end
                await last_response.close(timeout=2)

        async def _response_callback() -> None:
            # Wait for the recognition to be ready
            await recognizer_lock.wait()

            # Skip if no recognition
            if not recognizer_buffer or recognizer_buffer[-1] == "":
                return

            # Set recognition to be added to the call history
            logger.info("Voice recognized: %s", recognizer_buffer)
            nonlocal recognizer_store_next_recognition
            recognizer_store_next_recognition = True

            # Add recognition to the call history
            nonlocal last_response
            last_response = await scheduler.spawn(
                _out_answer(
                    call=call,
                    client=automation_client,
                    post_callback=post_callback,
                    scheduler=scheduler,
                    text=" ".join(recognizer_buffer),
                    training_callback=training_callback,
                )
            )

            # Clear the LLM queue
            recognizer_buffer.clear()

        await _in_audio(
            bits_per_sample=audio_bits_per_sample,
            channels=audio_channels,
            clear_audio_callback=_clear_audio_callback,
            in_stream=audio_stream,
            out_stream=recognizer_stream,
            response_callback=_response_callback,
            sample_rate=audio_sample_rate,
        )


# TODO: Refacto, this function is too long (and remove PLR0912/PLR0915 ignore)
@tracer.start_as_current_span("call_load_out_answer")
async def _out_answer(  # noqa: PLR0913, PLR0915
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: aiojobs.Scheduler,
    text: str,
    training_callback: Callable[[CallStateModel], Awaitable[None]],
    _iterations_remaining: int = 3,
) -> CallStateModel:
    """
    Handle the intelligence of the call, including: LLM chat, TTS, and media play.

    Play the loading sound while waiting for the intelligence to be processed. If the intelligence is not processed after few secs, play the timeout sound. If the intelligence is not processed after more secs, stop the intelligence processing and play the error sound.

    Returns the updated call model.
    """
    # Add span attributes
    span_attribute(CallAttributes.CALL_CHANNEL, "voice")
    span_attribute(CallAttributes.CALL_MESSAGE, call.messages[-1].content)

    # Reset recognition retry counter
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
        await scheduler.spawn(
            handle_play_text(
                call=call,
                client=client,
                style=style,
                text=text,
            )
        )

    # Chat
    chat_task = asyncio.create_task(
        _execute_llm_chat(
            call=call,
            client=client,
            post_callback=post_callback,
            text=text,
            tts_callback=_tts_callback,
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
            logger.debug("Chat task status: %s", chat_task.done())

            if chat_task.done():  # Break when chat coroutine is done
                # Clean up
                _clear_tasks()
                # Get result
                is_error, continue_chat, call = (
                    chat_task.result()
                )  # Store updated chat model
                await training_callback(call)  # Trigger trainings generation
                await _db.call_aset(
                    call
                )  # Save ASAP in DB allowing (1) user to cut off the Assistant and (2) SMS answers to be in order
                break

            if hard_timeout_task.done():  # Break when hard timeout is reached
                logger.warning(
                    "Hard timeout of %ss reached",
                    await answer_hard_timeout_sec(),
                )
                # Clean up
                _clear_tasks()
                break

            if play_loading_sound:  # Catch timeout if async loading is not started
                if (
                    soft_timeout_task.done() and not soft_timeout_triggered
                ):  # Speak when soft timeout is reached
                    logger.warning(
                        "Soft timeout of %ss reached",
                        await answer_soft_timeout_sec(),
                    )
                    soft_timeout_triggered = True
                    # Never store the error message in the call history, it has caused hallucinations in the LLM
                    await handle_play_text(
                        call=call,
                        client=client,
                        store=False,
                        text=await CONFIG.prompts.tts.timeout_loading(call),
                    )

                elif loading_task.done():  # Do not play timeout prompt plus loading, it can be frustrating for the user
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
            # Speak the error
            await _tts_callback(content, MessageStyleEnum.NONE)
            # Never store the error message in the call history, it has caused hallucinations in the LLM

        else:  # Retry chat after an error
            logger.info("Retrying chat, %s remaining", _iterations_remaining - 1)
            return await _out_answer(
                call=call,
                client=client,
                post_callback=post_callback,
                scheduler=scheduler,
                text=text,
                training_callback=training_callback,
                _iterations_remaining=_iterations_remaining - 1,
            )
    elif continue_chat and _iterations_remaining > 0:  # Contiue chat
        logger.info("Continuing chat, %s remaining", _iterations_remaining - 1)
        return await _out_answer(
            call=call,
            client=client,
            post_callback=post_callback,
            scheduler=scheduler,
            text=text,
            training_callback=training_callback,
            _iterations_remaining=_iterations_remaining - 1,
        )  # Recursive chat (like for for retry or tools)

        # End chat
        # TODO: Re-implement

    return call


# TODO: Refacto, this function is too long
@tracer.start_as_current_span("call_execute_llm_chat")
async def _execute_llm_chat(  # noqa: PLR0913, PLR0911, PLR0912, PLR0915
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    text: str,
    tts_callback: Callable[[str, MessageStyleEnum], Awaitable[None]],
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
    logger.info("Enhancing LLM chat with %s trainings", len(trainings))
    logger.debug("Trainings: %s", trainings)

    # System prompts
    system = CONFIG.prompts.llm.chat_system(
        call=call,
        trainings=trainings,
    )

    # Initialize TTS callbacks
    tts_callback = _tts_callback(
        automation_client=client,
        call=call,
    )

    # Build plugins
    plugins = DefaultPlugin(
        call=call,
        client=client,
        post_callback=post_callback,
        tts_callback=_buffer_callback,
    )

    tools = []
    if not use_tools:
        logger.warning("Tools disabled for this chat")
    else:
        tools = await plugins.to_openai()
        logger.debug("Tools: %s", tools)

    # Add user message in a temporary current context
    call_copy = call.model_copy()
    call_copy.messages.append(
        MessageModel(
            content=text,
            persona=MessagePersonaEnum.HUMAN,
        )
    )

    # Execute LLM inference
    maximum_tokens_reached = False
    content_buffer_pointer = 0
    tool_calls_buffer: dict[int, MessageToolModel] = {}
    try:
        async for delta in completion_stream(
            max_tokens=160,  # Lowest possible value for 90% of the cases, if not sufficient, retry will be triggered, 100 tokens ~= 75 words, 20 words ~= 1 sentence, 6 sentences ~= 160 tokens
            messages=call_copy.messages,
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
                for sentence, length in tts_sentence_split(
                    content_full[content_buffer_pointer:], False
                ):
                    content_buffer_pointer += length
                    plugins.style = await _content_callback(sentence, plugins.style)
    except MaximumTokensReachedError:  # Retry on maximum tokens reached
        logger.warning("Maximum tokens reached for this completion, retry asked")
        maximum_tokens_reached = True
    except APIError as e:  # Retry on API error
        logger.warning("OpenAI API call error: %s", e)
        return True, True, call  # Error, retry
    except SafetyCheckError as e:  # Last user message is trash, remove it
        logger.warning("Safety Check error: %s", e)
        if last_message := next(
            (
                call
                for call in reversed(call.messages)
                if call.persona == MessagePersonaEnum.HUMAN
                and call.action in [MessageAction.SMS, MessageAction.TALK]
            ),
            None,
        ):  # Remove last user message
            call.messages.remove(last_message)
        return True, False, call  # Error, no retry

    # Flush the remaining buffer
    if content_buffer_pointer < len(content_full):
        plugins.style = await _content_callback(
            content_full[content_buffer_pointer:], plugins.style
        )

    # Convert tool calls buffer
    tool_calls = [tool_call for _, tool_call in tool_calls_buffer.items()]

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    _, content_full = extract_message_style(remove_message_action(content_full))

    logger.debug("Chat response: %s", content_full)
    logger.debug("Tool calls: %s", tool_calls)

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
        call.messages.append(
            MessageModel(
                content=content_full.strip(),
                persona=MessagePersonaEnum.ASSISTANT,
                style=plugins.style,
                tool_calls=tool_calls,
            )
        )

    if tool_calls:  # Recusive call if needed
        return False, True, call

    if maximum_tokens_reached:  # Retry if maximum tokens reached
        return False, True, call  # TODO: Should we notify an error?

    return False, False, call  # No error, no retry


# TODO: Refacto and simplify
async def _in_audio(  # noqa: PLR0913
    bits_per_sample: int,
    channels: int,
    clear_audio_callback: Callable[[], Awaitable[None]],
    in_stream: asyncio.Queue[bytes],
    out_stream: PushAudioInputStream,
    response_callback: Callable[[], Awaitable[None]],
    sample_rate: int,
) -> None:
    clear_tts_task: asyncio.Task | None = None
    flush_task: asyncio.Task | None = None

    async def _flush_callback() -> None:
        """
        Flush the audio buffer if no audio is detected for a while.
        """
        nonlocal clear_tts_task

        # Wait for the timeout
        await asyncio.sleep(await vad_silence_timeout_ms() / 1000)

        # Cancel the TTS clear task if any
        if clear_tts_task:
            clear_tts_task.cancel()
            clear_tts_task = None

        logger.debug("Timeout triggered, flushing audio buffer")

        # Commit the buffer
        await response_callback()

    async def _clear_tts_callback() -> None:
        """
        Clear the TTS queue.

        Start is the index of the buffer where the TTS was triggered.
        """
        # Wait before clearing the TTS queue
        await asyncio.sleep(await vad_cutoff_timeout_ms() / 1000)

        logger.debug("Voice detected, cancelling TTS")

        # Clear the queue
        await clear_audio_callback()

    # Consumes audio stream
    while True:
        # Wait for the next audio packet
        in_chunck = await in_stream.get()

        # Load audio
        in_audio: AudioSegment = AudioSegment(
            channels=channels,
            data=in_chunck,
            frame_rate=sample_rate,
            sample_width=bits_per_sample // 8,
        )

        # Confirm ASAP that the event is processed
        in_stream.task_done()

        # Apply high-pass and low-pass filters in a simple attempt to reduce noise
        in_audio = high_pass_filter(in_audio, 200)
        in_audio = low_pass_filter(in_audio, 3000)

        # Always add the audio to the buffer
        assert isinstance(in_audio.raw_data, bytes)
        out_stream.write(in_audio.raw_data)

        # Get the relative dB, silences shoudl be at 1 to 5% of the max, so 0.1 to 0.5 of the threshold
        in_empty = False
        if (
            min(in_audio.rms / in_audio.max_possible_amplitude * 10, 1)
            < await vad_threshold()
        ):
            in_empty = True
            # Start timeout if not already started and VAD already triggered
            if not flush_task:
                flush_task = asyncio.create_task(_flush_callback())

        if in_empty:
            # Continue to the next audio packet
            continue

        # Voice detected, cancel the flush task if any
        if flush_task:
            flush_task.cancel()
            flush_task = None

        # Start the TTS clear task
        if not clear_tts_task:
            clear_tts_task = asyncio.create_task(_clear_tts_callback())


def _tts_callback(
    automation_client: CallAutomationClient,
    call: CallStateModel,
) -> Callable[[str, MessageStyleEnum], Awaitable[None]]:
    """
    Send back the TTS to the user.
    """

    @wraps(_tts_callback)
    async def wrapper(
        text: str,
        style: MessageStyleEnum,
    ) -> None:
        await asyncio.gather(
            handle_play_text(
                call=call,
                client=automation_client,
                style=style,
                text=text,
            ),  # First, play the TTS to the user
            _db.call_aset(
                call
            ),  # Second, save in DB allowing (1) user to cut off the Assistant and (2) SMS answers to be in order
        )

    return wrapper
