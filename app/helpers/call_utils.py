import asyncio
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from enum import Enum

import numpy as np
from aiojobs import Job, Scheduler
from azure.cognitiveservices.speech import (
    AudioConfig,
    SpeechConfig,
    SpeechRecognizer,
    SpeechSynthesisOutputFormat,
    SpeechSynthesizer,
)
from azure.cognitiveservices.speech.audio import (
    AudioOutputConfig,
    AudioStreamFormat,
    PushAudioInputStream,
    PushAudioOutputStream,
    PushAudioOutputStreamCallback,
)
from azure.communication.callautomation import (
    FileSource,
    PhoneNumberIdentifier,
    RecognitionChoice,
    RecognizeInputType,
    SsmlSource,
)
from azure.communication.callautomation._generated.models import (
    StartMediaStreamingRequest,
)
from azure.communication.callautomation.aio import (
    CallAutomationClient,
    CallConnectionClient,
)
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from noisereduce import reduce_noise

from app.helpers.cache import async_lru_cache
from app.helpers.config import CONFIG
from app.helpers.features import vad_threshold
from app.helpers.identity import token
from app.helpers.logging import logger
from app.models.call import CallStateModel
from app.models.message import (
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
)

_MAX_CHARACTERS_PER_TTS = 400  # Azure Speech Service TTS limit is 400 characters
_SENTENCE_PUNCTUATION_R = (
    r"([!?;]+|[\.\-:]+(?:$| ))"  # Split by sentence by punctuation
)
_TTS_SANITIZER_R = re.compile(
    r"[^\w\sÀ-ÿ'«»“”\"\"‘’''(),.!?;:\-\+_@/&€$%=]"  # noqa: RUF001
)  # Sanitize text for TTS

_db = CONFIG.database.instance()


class CallHangupException(Exception):
    """
    Exception raised when a call is hung up.
    """

    pass


class TtsCallback(PushAudioOutputStreamCallback):
    """
    Callback for Azure Speech Synthesizer to push audio data to a queue.
    """

    def __init__(self, queue: asyncio.Queue[bytes]):
        self.queue = queue

    def write(self, audio_buffer: memoryview) -> int:
        """
        Write audio data to the queue.
        """
        self.queue.put_nowait(audio_buffer.tobytes())
        return audio_buffer.nbytes

    def close(self) -> None:
        """
        Close the callback.
        """
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()


class ContextEnum(str, Enum):
    """
    Enum for call context.

    Used to track the operation context of a call in Azure Communication Services.
    """

    GOODBYE = "goodbye"
    """Hang up"""
    IVR_LANG_SELECT = "ivr_lang_select"
    """IVR language selection"""
    TRANSFER_FAILED = "transfer_failed"
    """Transfer failed"""


def tts_sentence_split(
    text: str, include_last: bool
) -> Generator[tuple[str, int], None, None]:
    """
    Split a text into sentences.

    Whitespaces are not returned, but punctiation is kept as it was in the original text.

    Example:
    - Input: "Hello, world! How are you? I'm fine. Thank you... Goodbye!"
    - Output: [("Hello, world!", 13), ("How are you?", 12), ("I'm fine.", 9), ("Thank you...", 13), ("Goodbye!", 8)]

    Returns a generator of tuples with the sentence and the original sentence length.
    """
    # Split by sentence by punctuation
    splits = re.split(_SENTENCE_PUNCTUATION_R, text)
    for i, split in enumerate(splits):
        # Skip punctuation
        if i % 2 == 1:
            continue
        # Skip empty lines
        if not split.strip():
            continue
        # Skip last line in case of missing punctuation
        if i == len(splits) - 1:
            if include_last:
                yield (
                    split.strip(),
                    len(split),
                )
        # Add punctuation back
        else:
            yield (
                split.strip() + splits[i + 1].strip(),
                len(split) + len(splits[i + 1]),
            )


async def handle_media(
    client: CallAutomationClient,
    call: CallStateModel,
    sound_url: str,
    context: ContextEnum | None = None,
) -> None:
    """
    Play a media to a call participant.

    If `context` is provided, it will be used to track the operation.
    """
    with _detect_hangup():
        assert call.voice_id, "Voice ID is required to control the call"
        call_client = await _use_call_client(client, call.voice_id)
        await call_client.play_media(
            operation_context=_context_serializer({context}),
            play_source=FileSource(url=sound_url),
        )


async def handle_automation_tts(  # noqa: PLR0913
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
    text: str,
    context: ContextEnum | None = None,
    store: bool = True,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
) -> None:
    """
    Play a text to a call participant.

    If `store` is `True`, the text will be stored in the call messages.

    If the call hangs up, the call will be ended.
    """
    assert call.voice_id, "Voice ID is required to control the call"

    # Play each chunk
    jobs: list[Job] = []
    chunks = _chunk_for_tts(text)
    call_client = await _use_call_client(client, call.voice_id)
    jobs += [
        await scheduler.spawn(
            _automation_play_text(
                call_client=call_client,
                call=call,
                context=context,
                style=style,
                text=chunk,
            )
        )
        for chunk in chunks
    ]

    # Wait for all jobs to finish and catch hangup
    for job in jobs:
        try:
            await job.wait()
        except CallHangupException:
            from app.helpers.call_events import hangup_now

            logger.info("Failed to play prompt, ending call now")
            await hangup_now(
                call=call,
                client=client,
                post_callback=post_callback,
                scheduler=scheduler,
            )
            return

    if store:
        await scheduler.spawn(
            _store_assistant_message(
                call=call,
                style=style,
                text=text,
                scheduler=scheduler,
            )
        )


async def _automation_play_text(
    call_client: CallConnectionClient,
    call: CallStateModel,
    context: ContextEnum | None,
    style: MessageStyleEnum,
    text: str,
) -> None:
    """
    Play a text to a call participant.

    If `context` is provided, it will be used to track the operation. Can raise a `CallHangupException` if the call is hung up.

    Returns `True` if the text was played, `False` otherwise.
    """
    logger.info("Playing TTS: %s", text)
    with _detect_hangup():
        assert call.voice_id, "Voice ID is required to control the call"
        await call_client.play_media(
            operation_context=_context_serializer({context}),
            play_source=_ssml_from_text(
                call=call,
                style=style,
                text=text,
            ),
        )


async def handle_realtime_tts(  # noqa: PLR0913
    call: CallStateModel,
    scheduler: Scheduler,
    text: str,
    tts_client: SpeechSynthesizer,
    store: bool = True,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
) -> None:
    """
    Play a text to the realtime TTS.

    If `store` is `True`, the text will be stored in the call messages.
    """
    # Play each chunk
    chunks = _chunk_for_tts(text)
    for chunk in chunks:
        logger.info("Playing TTS: %s", text)
        tts_client.speak_ssml_async(
            _ssml_from_text(
                call=call,
                style=style,
                text=chunk,
            ).ssml_text
        )

    if store:
        await scheduler.spawn(
            _store_assistant_message(
                call=call,
                style=style,
                text=text,
                scheduler=scheduler,
            )
        )


async def _store_assistant_message(
    call: CallStateModel,
    style: MessageStyleEnum,
    text: str,
    scheduler: Scheduler,
) -> None:
    """
    Store an assistant message in the call history.
    """
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.messages.append(
            MessageModel(
                content=text,
                persona=MessagePersonaEnum.ASSISTANT,
                style=style,
            )
        )


def _chunk_for_tts(
    text: str,
) -> list[str]:
    """
    Split a text in chunks and store them in the call messages.

    Chunks are separated by sentences and are limited to the TTS capacity.
    """
    # Sanitize text for TTS
    text = re.sub(_TTS_SANITIZER_R, " ", text)  # Remove unwanted characters
    text = re.sub(r"\s+", " ", text)  # Remove multiple spaces

    # Split text in chunks, separated by sentence
    chunks = []
    chunk = ""
    for to_add, _ in tts_sentence_split(text, True):
        # If chunck overflows TTS capacity, start a new record
        if len(chunk) + len(to_add) >= _MAX_CHARACTERS_PER_TTS:
            # Remove trailing space as sentences are separated by spaces
            chunks.append(chunk.strip())
            # Reset chunk
            chunk = ""
        # Add space to separate sentences
        chunk += to_add + " "

    # If there is a remaining chunk, add it
    if chunk:
        # Remove trailing space as sentences are separated by spaces
        chunks.append(chunk.strip())

    return chunks


def _ssml_from_text(
    call: CallStateModel,
    style: MessageStyleEnum,
    text: str,
) -> SsmlSource:
    """
    Generate an audio source that can be read by Azure Communication Services SDK.

    Text requires to be SVG escaped, and SSML tags are used to control the voice. Text is also truncated, as this is the limit of Azure Communication Services TTS, but a warning is logged.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-structure
    """
    if len(text) > _MAX_CHARACTERS_PER_TTS:
        logger.warning("Text is too long to be processed by TTS, truncating, fix this!")
        text = text[:_MAX_CHARACTERS_PER_TTS]
    # Escape text for SSML
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Build SSML tree
    ssml = f"""
    <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{call.lang.short_code}">
        <voice name="{call.lang.voice}" effect="eq_telecomhp8k">
            <lexicon uri="{CONFIG.resources.public_url}/lexicon.xml" />
            <lang xml:lang="{call.lang.short_code}">
                <mstts:express-as style="{style.value}" styledegree="0.5">
                    <prosody rate="{call.initiate.prosody_rate}">{text}</prosody>
                </mstts:express-as>
            </lang>
        </voice>
    </speak>
    """
    return SsmlSource(
        custom_voice_endpoint_id=call.lang.custom_voice_endpoint_id,
        ssml_text=ssml.strip(),
    )


async def handle_recognize_ivr(
    call: CallStateModel,
    choices: list[RecognitionChoice],
    client: CallAutomationClient,
    text: str,
    context: ContextEnum | None = None,
) -> None:
    """
    Recognize an IVR response after playing a text.

    Starts by playing text, then starts recognizing the response. The recognition will be interrupted by the user if they start speaking. The recognition will be played in the call language.
    """
    logger.info("Recognizing IVR: %s", text)
    try:
        assert call.voice_id, "Voice ID is required to control the call"
        call_client = await _use_call_client(client, call.voice_id)
        await call_client.start_recognizing_media(
            choices=choices,
            input_type=RecognizeInputType.CHOICES,
            interrupt_prompt=True,
            operation_context=_context_serializer({context}),
            play_prompt=_ssml_from_text(
                call=call,
                style=MessageStyleEnum.NONE,
                text=text,
            ),
            speech_language=call.lang.short_code,
            target_participant=PhoneNumberIdentifier(call.initiate.phone_number),  # pyright: ignore
        )
    except ResourceNotFoundError:
        logger.debug("Call hung up before recognizing")


async def handle_hangup(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    """
    Hang up a call.

    If the call is already hung up, the exception will be suppressed.
    """
    logger.info("Hanging up")
    with (
        # Suppress hangup exception
        suppress(CallHangupException),
        # Detect hangup exception
        _detect_hangup(),
    ):
        assert call.voice_id, "Voice ID is required to control the call"
        call_client = await _use_call_client(client, call.voice_id)
        await call_client.hang_up(is_for_everyone=True)


async def handle_transfer(
    client: CallAutomationClient,
    call: CallStateModel,
    target: str,
    context: ContextEnum | None = None,
) -> None:
    """
    Transfer a call to another participant.

    Can raise a `CallHangupException` if the call is hung up.
    """
    logger.info("Transferring call: %s", target)
    with _detect_hangup():
        assert call.voice_id, "Voice ID is required to control the call"
        call_client = await _use_call_client(client, call.voice_id)
        await call_client.transfer_call_to_participant(
            operation_context=_context_serializer({context}),
            target_participant=PhoneNumberIdentifier(target),
        )


async def start_audio_streaming(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    """
    Start audio streaming to the call.

    Can raise a `CallHangupException` if the call is hung up.
    """
    logger.info("Starting audio streaming")
    with _detect_hangup():
        assert call.voice_id, "Voice ID is required to control the call"
        call_client = await _use_call_client(client, call.voice_id)
        # TODO: Use the public API once the "await" have been fixed
        # await call_client.start_media_streaming()
        await call_client._call_media_client.start_media_streaming(
            call_connection_id=call_client._call_connection_id,
            start_media_streaming_request=StartMediaStreamingRequest(),
        )


async def stop_audio_streaming(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    """
    Stop audio streaming to the call.

    Can raise a `CallHangupException` if the call is hung up.
    """
    logger.info("Stopping audio streaming")
    with _detect_hangup():
        assert call.voice_id, "Voice ID is required to control the call"
        call_client = await _use_call_client(client, call.voice_id)
        await call_client.stop_media_streaming()


def _context_serializer(contexts: set[ContextEnum | None] | None) -> str | None:
    """
    Serialize a set of contexts to a JSON string.

    Returns `None` if no context is provided.
    """
    if not contexts:
        return None
    return json.dumps([context.value for context in contexts if context])


@contextmanager
def _detect_hangup() -> Generator[None, None, None]:
    """
    Catch a call hangup and raise a `CallHangupException` instead of the Call Automation SDK exceptions.
    """
    try:
        yield
    except ResourceNotFoundError:
        logger.debug("Call hung up")
        raise CallHangupException
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug("Call hung up")
            raise CallHangupException
        else:
            raise e


@async_lru_cache()
async def _use_call_client(
    client: CallAutomationClient, voice_id: str
) -> CallConnectionClient:
    """
    Return the call client for a given call.
    """
    logger.debug("Using Call client for %s", voice_id)

    return client.get_call_connection(call_connection_id=voice_id)


@asynccontextmanager
async def use_tts_client(
    call: CallStateModel,
    out: asyncio.Queue[bytes],
) -> AsyncGenerator[SpeechSynthesizer, None]:
    """
    Use a text-to-speech client for a call.

    Output format is in PCM 16-bit, 16 kHz, 1 channel.

    Yields a client to push audio data to the queue. Once the context is exited, the client will stop.
    """
    # Get AAD token
    aad_token = await (await token("https://cognitiveservices.azure.com/.default"))()

    # Create real-time client
    # TODO: Use v2 endpoint (https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency?pivots=programming-language-python#how-to-use-text-streaming) but seems compatible with AAD auth? Found nothing in the docs (https://github.com/Azure-Samples/cognitive-services-speech-sdk/blob/e392c9ca09d44ebd65081e7cb44593a2b16cd5a7/samples/python/web/avatar/app.py#L137).
    config = SpeechConfig(
        endpoint=f"wss://{CONFIG.cognitive_service.region}.tts.speech.microsoft.com/cognitiveservices/websocket/v1",
        speech_recognition_language=call.lang.short_code,
    )
    config.authorization_token = (
        f"aad#{CONFIG.cognitive_service.resource_id}#{aad_token}"
    )
    config.speech_synthesis_voice_name = call.lang.voice
    config.set_speech_synthesis_output_format(
        SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
    )
    if call.lang.custom_voice_endpoint_id:
        config.endpoint_id = call.lang.custom_voice_endpoint_id
    # TODO: How to close the client?
    client = SpeechSynthesizer(
        speech_config=config,
        audio_config=AudioOutputConfig(stream=PushAudioOutputStream(TtsCallback(out))),
    )

    # Connect events
    client.synthesis_started.connect(lambda _: logger.debug("TTS started"))
    client.synthesis_completed.connect(lambda _: logger.debug("TTS completed"))

    # Return
    yield client


@asynccontextmanager
async def use_stt_client(
    audio_sample_rate: int,
    call: CallStateModel,
    complete_callback: Callable[[str], None],
    partial_callback: Callable[[str], None],
) -> AsyncGenerator[PushAudioInputStream, None]:
    """
    Use a speech-to-text client for a call.

    Input format is in PCM 16-bit, 16 kHz, 1 channel.

    Yields a stream to push audio data to the client. Once the context is exited, the client will stop.
    """
    # Get AAD token
    aad_token = await (await token("https://cognitiveservices.azure.com/.default"))()

    # Create client
    stream = PushAudioInputStream(
        # PCM 16-bit, 1 channel
        stream_format=AudioStreamFormat(
            bits_per_sample=16,
            channels=1,
            samples_per_second=audio_sample_rate,
        ),
    )
    client = SpeechRecognizer(
        audio_config=AudioConfig(stream=stream),
        language=call.lang.short_code,
        speech_config=SpeechConfig(
            auth_token=f"aad#{CONFIG.cognitive_service.resource_id}#{aad_token}",
            region=CONFIG.cognitive_service.region,
        ),
    )

    # TSS events
    client.recognized.connect(
        lambda e: complete_callback(e.result.text) if e.result.text else None
    )
    client.recognizing.connect(
        lambda e: partial_callback(e.result.text) if e.result.text else None
    )

    # Debugging events
    client.canceled.connect(lambda event: logger.warning("STT cancelled: %s", event))
    client.session_started.connect(lambda _: logger.debug("STT started"))
    client.session_stopped.connect(lambda _: logger.debug("STT stopped"))

    try:
        # Start STT
        client.start_continuous_recognition_async()
        # Return
        yield stream
    finally:
        # Stop STT
        client.stop_continuous_recognition_async()


class EchoCancellationStream:
    """
    Real-time audio stream with echo cancellation.

    Input and output formats are in PCM 16-bit, 16 kHz, 1 channel.
    """

    _chunk_size: int
    _input_queue: asyncio.Queue[bytes] = asyncio.Queue()
    _output_queue: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue()
    _packet_duration_ms: int
    _packet_size: int
    _reference_queue: asyncio.Queue[bytes] = asyncio.Queue()
    _sample_rate: int

    def __init__(
        self,
        sample_rate: int,
        max_delay_ms: int = 200,
        packet_duration_ms: int = 20,
    ):
        self._packet_duration_ms = packet_duration_ms
        self._sample_rate = sample_rate

        max_delay_samples = int(max_delay_ms / 1000 * self._sample_rate)
        self._bot_voice_buffer = np.zeros(max_delay_samples, dtype=np.float32)

        self._chunk_size = int(self._sample_rate * self._packet_duration_ms / 1000)
        self._packet_size = self._chunk_size * 2  # Each sample is 2 bytes (PCM 16-bit)

    def _pcm_to_float(self, pcm: bytes) -> np.ndarray:
        """
        Convert PCM 16-bit to float (-1.0 to 1.0).
        """
        return (
            np.frombuffer(
                buffer=pcm,
                dtype=np.int16,
            ).astype(np.float32)
            / 32768.0
        )

    def _float_to_pcm(self, floats: np.ndarray) -> bytes:
        """
        Convert float (-1.0 to 1.0) to PCM 16-bit.
        """
        pcm = (floats * 32767).clip(-32768, 32767).astype(np.int16)
        return pcm.tobytes()

    def _update_input_buffer(self, voice: np.ndarray) -> None:
        """
        Update the rolling buffer for the input voice.
        """
        buffer_length = len(self._bot_voice_buffer)
        reference_length = len(voice)

        if reference_length >= buffer_length:
            # If the reference is longer than the buffer, keep the most recent samples
            self._bot_voice_buffer = voice[-buffer_length:]
        else:
            # Append new samples and keep the buffer size fixed
            self._bot_voice_buffer = np.roll(self._bot_voice_buffer, -reference_length)
            self._bot_voice_buffer[-reference_length:] = voice

    async def _rms_speech_detection(self, voice: np.ndarray) -> bool:
        """
        Simple speech detection based on RMS (acoustic pressure).

        Returns True if speech is detected, False otherwise.
        """
        # Calculate Root Mean Square (RMS)
        rms = np.sqrt(np.mean(voice**2))
        # Get VAD threshold, divide by 10 to more usability from user side, as RMS is in range 0-1 and a detection of 0.1 is a good maximum threshold
        threshold = await vad_threshold() / 10
        return rms >= threshold

    async def _process_one(self, input_pcm: bytes) -> None:
        """
        Process one audio chunk with echo cancellation.
        """
        # Use silence as the reference if none is available
        if self._reference_queue.empty():
            reference_pcm = b"\x00" * self._packet_size
        else:
            reference_pcm = await self._reference_queue.get()
            self._reference_queue.task_done()

        # Convert PCM to float for processing
        input_signal = self._pcm_to_float(input_pcm)
        reference_signal = self._pcm_to_float(reference_pcm)

        # Update the input buffer with the reference signal
        self._update_input_buffer(reference_signal)

        # Apply noise reduction
        reduced_signal = reduce_noise(
            # Input signal
            clip_noise_stationary=False,
            sr=self._sample_rate,
            y=input_signal,
            # Performance
            n_fft=self._chunk_size,
            # Since the reference signal is already noise-reduced, we can assume it's stationary
            stationary=True,
            y_noise=self._bot_voice_buffer,
            # Output quality
            prop_decrease=0.75,  # Reduce noise by 75%
        )

        # Perform VAD test
        input_speaking = await self._rms_speech_detection(reduced_signal)

        # Convert processed float signal back to PCM
        processed_pcm = self._float_to_pcm(reduced_signal)

        # Add processed PCM and metadata to the output queue
        await self._output_queue.put((processed_pcm, input_speaking))

    async def process_stream(self) -> None:
        """
        Process the audio stream in real-time.
        """
        async with Scheduler(
            limit=5,  # Allow 5 concurrent tasks
        ) as scheduler:
            while True:
                # Fetch input audio
                input_pcm = await self._input_queue.get()
                self._input_queue.task_done()

                # Queue the processing
                await scheduler.spawn(
                    asyncio.wait_for(
                        self._process_one(input_pcm),
                        timeout=self._packet_duration_ms
                        / 1000
                        * 5,  # Allow temporary high latency
                    )
                )

    async def push_input(self, audio_data: bytes) -> None:
        """
        Push PCM input audio into the input queue.
        """
        if len(audio_data) != self._packet_size:
            raise ValueError(
                f"Expected packet size {self._packet_size} bytes, got {len(audio_data)} bytes."
            )
        await self._input_queue.put(audio_data)

    async def push_reference(self, audio_data: bytes) -> None:
        """
        Push PCM reference audio into the reference queue.

        The reference audio is used for echo cancellation.
        """
        # Extract packets and pad them if necessary
        buffer_pointer = 0
        while buffer_pointer < len(audio_data):
            chunk = audio_data[: self._packet_size].ljust(self._packet_size, b"\x00")
            await self._reference_queue.put(chunk)
            buffer_pointer += self._packet_size

    async def pull_audio(self) -> tuple[bytes, bool]:
        """
        Pull processed PCM audio and metadata from the output queue.

        Returns a tuple with the echo-cancelled PCM audio and a boolean flag indicating if the user was speaking.
        """
        # return await self._output_queue.get()
        try:
            return await asyncio.wait_for(
                fut=self._output_queue.get(),
                timeout=self._packet_duration_ms
                / 1000
                * 1.5,  # Allow temporary small latency
            )
        except TimeoutError:
            return b"\x00" * self._packet_size, False  # Silence PCM chunk and no speech
