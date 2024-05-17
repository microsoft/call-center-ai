from contextlib import asynccontextmanager
from enum import Enum
from helpers.config import CONFIG
from helpers.logging import build_logger
from models.call import CallStateModel
from models.message import StyleEnum as MessageStyleEnum
from typing import AsyncGenerator, Generator, Optional
from azure.communication.callautomation import (
    CallAutomationClient,
    CallConnectionClient,
    FileSource,
    PhoneNumberIdentifier,
    RecognitionChoice,
    RecognizeInputType,
    SsmlSource,
)
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from models.message import (
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
)
import re


_logger = build_logger(__name__)
_SENTENCE_PUNCTUATION_R = r"(\. |\.$|[!?;])"  # Split by sentence by punctuation
_TTS_SANITIZER_R = re.compile(
    r"[^\w\s'«»“”\"\"‘’''(),.!?;:\-\+_@/]"
)  # Sanitize text for TTS


class ContextEnum(str, Enum):
    """
    Enum for call context.

    Used to track the operation context of a call in Azure Communication Services.
    """

    CONNECT_AGENT = "connect_agent"  # Transfer to agent
    GOODBYE = "goodbye"  # Hang up
    TRANSFER_FAILED = "transfer_failed"  # Transfer failed


def tts_sentence_split(text: str, include_last: bool) -> Generator[str, None, None]:
    """
    Split a text into sentences.
    """
    # Split by sentence by punctuation
    splits = re.split(_SENTENCE_PUNCTUATION_R, text)
    for i, split in enumerate(splits):
        if i % 2 == 1:  # Skip punctuation
            continue
        if not split:  # Skip empty lines
            continue
        if i == len(splits) - 1:  # Skip last line in case of missing punctuation
            if include_last:
                yield split
        else:  # Add punctuation back
            yield split + splits[i + 1]


# TODO: Disable or lower profanity filter. The filter seems enabled by default, it replaces words like "holes in my roof" by "*** in my roof". This is not acceptable for a call center.
async def _handle_recognize_media(
    client: CallAutomationClient,
    call: CallStateModel,
    sound_url: str,
) -> None:
    """
    Play a media to a call participant and start recognizing the response.
    """
    _logger.debug(f"Recognizing media")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            call_client.start_recognizing_media(
                end_silence_timeout=3,  # Sometimes user includes breaks in their speech
                input_type=RecognizeInputType.SPEECH,
                play_prompt=FileSource(url=sound_url),
                speech_language=call.lang.short_code,
                target_participant=PhoneNumberIdentifier(call.phone_number),  # type: ignore
            )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before recognizing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            _logger.debug(f"Call hung up before playing")
        else:
            raise e


async def handle_media(
    client: CallAutomationClient,
    call: CallStateModel,
    sound_url: str,
    context: Optional[str] = None,
) -> None:
    """
    Play a media to a call participant.

    If `context` is provided, it will be used to track the operation.
    """
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            call_client.play_media(
                operation_context=context,
                play_source=FileSource(url=sound_url),
            )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            _logger.debug(f"Call hung up before playing")
        else:
            raise e


async def handle_recognize_text(
    client: CallAutomationClient,
    call: CallStateModel,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
    text: Optional[str] = None,
    store: bool = True,
) -> None:
    """
    Play a text to a call participant and start recognizing the response.

    If `store` is `True`, the text will be stored in the call messages. Starts by playing text, then the "ready" sound, and finally starts recognizing the response.
    """
    if text:
        await handle_play(
            call=call,
            client=client,
            store=store,
            style=style,
            text=text,
        )

    await _handle_recognize_media(
        call=call,
        client=client,
        sound_url=CONFIG.prompts.sounds.ready(),
    )


async def handle_play(
    client: CallAutomationClient,
    call: CallStateModel,
    text: str,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
    context: Optional[str] = None,
    store: bool = True,
) -> None:
    """
    Play a text to a call participant.

    If store is True, the text will be stored in the call messages. Compatible with text larger than 400 characters, in that case the text will be split in chunks and played sequentially.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
    """
    # Sanitize text for TTS
    text = re.sub(_TTS_SANITIZER_R, "", text)

    # Store text in call messages
    if store:
        if (
            call.messages and call.messages[-1].persona == MessagePersonaEnum.ASSISTANT
        ):  # If the last message was from the assistant, append to it
            call.messages[-1].content += f" {text}"
        else:  # Otherwise, create a new message
            call.messages.append(
                MessageModel(
                    content=text,
                    persona=MessagePersonaEnum.ASSISTANT,
                    style=style,
                )
            )

    # Split text in chunks of max 400 characters, separated by sentence
    chunks = []
    chunk = ""
    for to_add in tts_sentence_split(text, True):
        if len(chunk) + len(to_add) >= 400:
            chunks.append(chunk.strip())  # Remove trailing space
            chunk = ""
        chunk += to_add
    if chunk:
        chunks.append(chunk)

    # Play each chunk
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            for chunk in chunks:
                _logger.info(f"Playing text: {text} ({style})")
                call_client.play_media(
                    operation_context=context,
                    play_source=_audio_from_text(chunk, style, call),
                )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            _logger.debug(f"Call hung up before playing")
        else:
            raise e


def _audio_from_text(
    text: str, style: MessageStyleEnum, call: CallStateModel
) -> SsmlSource:
    """
    Generate an audio source that can be read by Azure Communication Services SDK.

    Text requires to be SVG escaped, and SSML tags are used to control the voice. Plus, text is slowed down by 5% to make it more understandable for elderly people. Text is also truncated to 400 characters, as this is the limit of Azure Communication Services TTS, but a warning is logged.
    """
    # Azure Speech Service TTS limit is 400 characters
    if len(text) > 400:
        _logger.warning(
            f"Text is too long to be processed by TTS, truncating to 400 characters, fix this!"
        )
        text = text[:400]
    ssml = f"""
    <speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{call.lang.short_code}">
        <voice name="{call.lang.voice}" effect="eq_telecomhp8k">
            <lexicon uri="{CONFIG.resources.public_url}/lexicon.xml" />
            <mstts:express-as style="{style.value}" styledegree="0.5">
                <prosody rate="0.95">{text}</prosody>
            </mstts:express-as>
        </voice>
    </speak>
    """
    return SsmlSource(ssml_text=ssml.strip())


async def handle_recognize_ivr(
    client: CallAutomationClient,
    call: CallStateModel,
    text: str,
    choices: list[RecognitionChoice],
) -> None:
    """
    Recognize an IVR response after playing a text.

    Starts by playing text, then starts recognizing the response. The recognition will be interrupted by the user if they start speaking. The recognition will be played in the call language.
    """
    _logger.info(f"Playing text before IVR: {text}")
    _logger.debug(f"Recognizing IVR")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            call_client.start_recognizing_media(
                choices=choices,
                end_silence_timeout=20,
                input_type=RecognizeInputType.CHOICES,
                interrupt_prompt=True,
                play_prompt=_audio_from_text(text, MessageStyleEnum.NONE, call),
                speech_language=call.lang.short_code,
                target_participant=PhoneNumberIdentifier(call.phone_number),  # type: ignore
            )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before recognizing")


async def handle_hangup(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    _logger.debug("Hanging up call")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            call_client.hang_up(is_for_everyone=True)
    except ResourceNotFoundError:
        _logger.debug("Call already hung up")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            _logger.debug("Call hung up before playing")
        else:
            raise e


async def handle_transfer(
    client: CallAutomationClient,
    call: CallStateModel,
    target: str,
    context: Optional[str] = None,
) -> None:
    _logger.debug(f"Transferring call to {target}")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            call_client.transfer_call_to_participant(
                operation_context=context,
                target_participant=PhoneNumberIdentifier(target),
            )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before transferring")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            _logger.debug(f"Call hung up before transferring")
        else:
            raise e


@asynccontextmanager
async def _use_call_client(
    client: CallAutomationClient, voice_id: str
) -> AsyncGenerator[CallConnectionClient, None]:
    yield client.get_call_connection(call_connection_id=voice_id)
