import asyncio
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager, contextmanager, suppress
from enum import Enum

from aiojobs import Job, Scheduler
from azure.cognitiveservices.speech import (
    AudioConfig,
    SpeechConfig,
    SpeechRecognizer,
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

from app.helpers.cache import async_lru_cache
from app.helpers.config import CONFIG
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

    def __init__(self, queue: asyncio.Queue[bytes | bool]):
        self.queue = queue

    def write(self, audio_buffer: memoryview) -> int:
        self.queue.put_nowait(audio_buffer.tobytes())
        return audio_buffer.nbytes


class ContextEnum(str, Enum):
    """
    Enum for call context.

    Used to track the operation context of a call in Azure Communication Services.
    """

    GOODBYE = "goodbye"
    """Hang up"""
    IVR_LANG_SELECT = "ivr_lang_select"
    """IVR language selection"""
    START_REALTIME = "start_realtime"
    """Start realtime call"""
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
    audio: asyncio.Queue[bytes | bool],
    call: CallStateModel,
) -> AsyncGenerator[SpeechSynthesizer, None]:
    """
    Use a text-to-speech client for a call.
    """
    # Get AAD token
    aad_token = await (await token("https://cognitiveservices.azure.com/.default"))()

    # Create real-time client
    # TODO: Use v2 endpoint (https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-lower-speech-synthesis-latency?pivots=programming-language-python#how-to-use-text-streaming) but seems compatible with AAD auth? Found nothing in the docs (https://github.com/Azure-Samples/cognitive-services-speech-sdk/blob/e392c9ca09d44ebd65081e7cb44593a2b16cd5a7/samples/python/web/avatar/app.py#L137).
    config = SpeechConfig(
        endpoint=f"wss://{CONFIG.cognitive_service.region}.tts.speech.microsoft.com/cognitiveservices/websocket/v1",
    )
    config.authorization_token = (
        f"aad#{CONFIG.cognitive_service.resource_id}#{aad_token}"
    )
    config.speech_synthesis_voice_name = call.lang.voice
    if call.lang.custom_voice_endpoint_id:
        config.endpoint_id = call.lang.custom_voice_endpoint_id
    # TODO: How to close the client?
    client = SpeechSynthesizer(
        speech_config=config,
        audio_config=AudioOutputConfig(
            stream=PushAudioOutputStream(TtsCallback(audio))
        ),
    )

    # Connect events
    client.synthesis_started.connect(lambda _: logger.debug("TTS started"))
    client.synthesis_completed.connect(lambda _: logger.debug("TTS completed"))

    # Return
    yield client


@asynccontextmanager
async def use_stt_client(
    audio_bits_per_sample: int,
    audio_channels: int,
    audio_sample_rate: int,
    call: CallStateModel,
    callback: Callable[[str], None],
) -> AsyncGenerator[PushAudioInputStream, None]:
    """
    Use a speech-to-text client for a call.

    Yields a stream to push audio data to the client. Once the context is exited, the client will stop.
    """
    # Get AAD token
    aad_token = await (await token("https://cognitiveservices.azure.com/.default"))()

    # Create client
    stream = PushAudioInputStream(
        stream_format=AudioStreamFormat(
            bits_per_sample=audio_bits_per_sample,
            channels=audio_channels,
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

    # Connect events
    client.recognized.connect(lambda e: callback(e.result.text))
    client.session_started.connect(lambda _: logger.debug("STT started"))
    client.session_stopped.connect(lambda _: logger.debug("STT stopped"))
    client.canceled.connect(lambda event: logger.warning("STT cancelled: %s", event))

    try:
        # Start STT
        client.start_continuous_recognition_async()
        # Return
        yield stream
    finally:
        # Stop STT
        client.stop_continuous_recognition_async()
