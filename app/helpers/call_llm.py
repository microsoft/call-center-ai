import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps

import aiojobs
from azure.communication.callautomation.aio import CallAutomationClient
from pydub import AudioSegment
from rtclient import (
    RTClient,
    RTFunctionCallItem,
    RTInputAudioItem,
    RTMessageItem,
    RTResponse,
    RTTextContent,
)

from app.helpers.call_utils import (
    handle_clear_queue,
    handle_play_text,
    tts_sentence_split,
)
from app.helpers.config import CONFIG
from app.helpers.features import vad_silence_timeout_ms, vad_threshold
from app.helpers.llm_tools import DefaultPlugin
from app.helpers.llm_utils import AbstractPlugin
from app.helpers.llm_worker import completion_realtime
from app.helpers.logging import logger
from app.helpers.monitoring import CallAttributes, span_attribute, tracer
from app.models.call import CallStateModel
from app.models.message import (
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
    ToolModel as MessageToolModel,
    extract_message_style,
    remove_message_action,
)

_db = CONFIG.database.instance()


# TODO: Refacto and simplify
@tracer.start_as_current_span("call_load_llm_chat")
async def load_llm_chat(  # noqa: PLR0913
    audio_bits_per_sample: int,
    audio_channels: int,
    audio_sample_rate: int,
    audio_stream: asyncio.Queue[bytes],
    automation_client: CallAutomationClient,
    call: CallStateModel,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    # System prompts
    system = CONFIG.prompts.llm.chat_system(call)

    # Initialize TTS callbacks
    tts_callback = _tts_callback(
        automation_client=automation_client,
        call=call,
    )

    # Build plugins
    plugin = DefaultPlugin(
        call=call,
        client=automation_client,
        post_callback=post_callback,
    )
    tools = await plugin.to_rtclient()

    # Build client
    async with (
        completion_realtime(
            max_tokens=160,  # Lowest possible value for 90% of the cases, if not sufficient, retry will be triggered, 100 tokens ~= 75 words, 20 words ~= 1 sentence, 6 sentences ~= 160 tokens
            messages=call.messages,
            system=system,
            tools=tools,
        ) as client,
        aiojobs.Scheduler() as scheduler,
    ):
        await _in_audio(
            automation_client=automation_client,
            bits_per_sample=audio_bits_per_sample,
            call=call,
            channels=audio_channels,
            client=client,
            plugin=plugin,
            sample_rate=audio_sample_rate,
            scheduler=scheduler,
            stream=audio_stream,
            tts_callback=tts_callback,
        )


async def _out_message_item(
    event: RTMessageItem,
    tts_callback: Callable[[str, MessageStyleEnum], Awaitable[None]],
) -> None:
    content_buffer_pointer = 0
    content_full = ""
    style = MessageStyleEnum.NONE

    async def _content_callback(
        buffer: str, style: MessageStyleEnum
    ) -> MessageStyleEnum:
        """
        Clean text buffer and send to TTS if necessary.

        If the message is the first one, the TTS is interrupted.
        """
        # Remove tool calls from buffer content and detect style
        local_style, local_content = extract_message_style(
            remove_message_action(buffer)
        )
        new_style = local_style or style
        if local_content:
            await tts_callback(local_content, new_style)
        return new_style

    # Consume responses
    async for content_part in event:
        # Skip non-text content
        if not isinstance(content_part, RTTextContent):
            continue
        # Consume text stream
        async for chunck in content_part.text_chunks():
            content_full += chunck
            # Return to TTS as soon as a sentence is ready
            for sentence, length in tts_sentence_split(
                content_full[content_buffer_pointer:], False
            ):
                # Update buffer pointer
                content_buffer_pointer += length
                # Send sentence to TTS and update style
                style = await _content_callback(sentence, style)


async def _out_function_call_item(
    event: RTFunctionCallItem,
    plugin: AbstractPlugin,
) -> None:
    # Consume and parse the event
    await event
    tool = MessageToolModel(
        function_name=event.function_name,
        function_arguments=event.arguments,
        tool_id=event.call_id,
    )
    # Execute
    await tool.execute_function(plugin)
    # Update call model if object reference changed
    # TOOD: Uncomment when the shared reference is implemented
    # call = plugins.call


async def _out_response(
    events: RTResponse,
    plugin: AbstractPlugin,
    scheduler: aiojobs.Scheduler,
    tts_callback: Callable[[str, MessageStyleEnum], Awaitable[None]],
) -> None:
    async for event in events:
        match event.type:
            case "message":
                await scheduler.spawn(
                    _out_message_item(
                        event=event,
                        tts_callback=tts_callback,
                    )
                )
            case "function_call":
                await scheduler.spawn(
                    _out_function_call_item(
                        event=event,
                        plugin=plugin,
                    )
                )


async def _out_input_item(
    call: CallStateModel,
    event: RTInputAudioItem,
) -> None:
    # Consume event and extract content
    await event
    text = (event.transcript or "").strip()

    # Skip if no transcription
    if not text:
        return

    # Log
    logger.info("Voice recognition: %s", text)
    span_attribute(CallAttributes.CALL_CHANNEL, "voice")
    span_attribute(CallAttributes.CALL_MESSAGE, text)

    # Update call history
    call.messages.append(
        MessageModel(
            content=text,
            persona=MessagePersonaEnum.HUMAN,
        )
    )


# TODO: Refacto and simplify
async def _in_audio(  # noqa: PLR0913
    automation_client: CallAutomationClient,
    bits_per_sample: int,
    call: CallStateModel,
    channels: int,
    client: RTClient,
    plugin: AbstractPlugin,
    sample_rate: int,
    scheduler: aiojobs.Scheduler,
    stream: asyncio.Queue[bytes],
    tts_callback: Callable[[str, MessageStyleEnum], Awaitable[None]],
) -> None:
    clear_tts_task: asyncio.Task | None = None
    flush_task: asyncio.Task | None = None
    rms_threshold = await vad_threshold()
    sample_width = bits_per_sample // 8
    silence_duration_ms = await vad_silence_timeout_ms()

    async def _flush_callback() -> None:
        """
        Flush the audio buffer if no audio is detected for a while.
        """
        nonlocal clear_tts_task

        # Wait for the timeout
        await asyncio.sleep(silence_duration_ms / 1000)

        # Cancel the TTS clear task if any
        if clear_tts_task:
            clear_tts_task.cancel()
            clear_tts_task = None

        logger.debug("Timeout triggered, flushing audio buffer")

        # Commit the buffer
        input_item = await client.commit_audio()
        response = await client.generate_response()
        await scheduler.spawn(
            _out_response(
                events=response,
                plugin=plugin,
                scheduler=scheduler,
                tts_callback=tts_callback,
            )
        )
        await scheduler.spawn(
            _out_input_item(
                call=call,
                event=input_item,
            )
        )

    async def _clear_tts_callback() -> None:
        """
        Clear the TTS queue.

        Start is the index of the buffer where the TTS was triggered.
        """
        # Wait 200ms before clearing the TTS queue
        await asyncio.sleep(0.2)

        logger.debug("Voice detected, cancelling TTS")

        # Clear the LLM queue
        await scheduler.spawn(client.clear_audio())

        # Clear the TTS queue
        await scheduler.spawn(
            handle_clear_queue(
                call=call,
                client=automation_client,
            )
        )

    # Consumes audio stream
    while True:
        # Wait for the next audio packet
        in_chunck = await stream.get()

        # Load audio
        in_audio: AudioSegment = AudioSegment(
            channels=channels,
            data=in_chunck,
            frame_rate=sample_rate,
            sample_width=sample_width,
        )

        # Confirm ASAP that the event is processed
        stream.task_done()

        # Get the relative dB, silences shoudl be at 1 to 5% of the max, so 0.1 to 0.5 of the threshold
        in_empty = False
        if min(in_audio.rms / in_audio.max_possible_amplitude * 10, 1) < rms_threshold:
            in_empty = True
            # Start timeout if not already started and VAD already triggered
            if not flush_task:
                flush_task = asyncio.create_task(_flush_callback())

        # Convert to a compatible format for the LLM
        # See: https://platform.openai.com/docs/guides/realtime?text-generation-quickstart-example=text#audio-formats
        out_audio = in_audio.set_frame_rate(24000).set_channels(1).set_sample_width(2)

        if in_empty:
            # Voice not detected, add the audio to the buffer
            assert isinstance(out_audio.raw_data, bytes)
            await scheduler.spawn(client.send_audio(out_audio.raw_data))
            # Continue to the next audio packet
            continue

        # Voice detected, cancel the flush task if any
        if flush_task:
            flush_task.cancel()
            flush_task = None

        # Start the TTS clear task
        if not clear_tts_task:
            clear_tts_task = asyncio.create_task(_clear_tts_callback())

        # Send voice to the LLM
        assert isinstance(out_audio.raw_data, bytes)
        await scheduler.spawn(client.send_audio(out_audio.raw_data))


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
