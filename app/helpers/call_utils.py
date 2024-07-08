from contextlib import asynccontextmanager
from enum import Enum
from helpers.config import CONFIG
from helpers.logging import logger
from models.call import CallStateModel
from models.message import StyleEnum as MessageStyleEnum
from typing import AsyncGenerator, Generator, Optional
from azure.communication.callautomation import (
    FileSource,
    PhoneNumberIdentifier,
    RecognitionChoice,
    RecognizeInputType,
    SsmlSource,
)
from azure.communication.callautomation.aio import (
    CallAutomationClient,
    CallConnectionClient,
)
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
from models.message import (
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    StyleEnum as MessageStyleEnum,
)
import re
import json


_SENTENCE_PUNCTUATION_R = (
    r"([!?;]+|[\.\-:]+(?:$| ))"  # Split by sentence by punctuation
)
_TTS_SANITIZER_R = re.compile(
    r"[^\w\sÀ-ÿ'«»“”\"\"‘’''(),.!?;:\-\+_@/&<>€$%=*]"
)  # Sanitize text for TTS


class ContextEnum(str, Enum):
    """
    Enum for call context.

    Used to track the operation context of a call in Azure Communication Services.
    """

    CONNECT_AGENT = "connect_agent"  # Transfer to agent
    GOODBYE = "goodbye"  # Hang up
    IVR_LANG_SELECT = "ivr_lang_select"  # IVR language selection
    TRANSFER_FAILED = "transfer_failed"  # Transfer failed


def tts_sentence_split(text: str, include_last: bool) -> Generator[str, None, None]:
    """
    Split a text into sentences.
    """
    # Split by sentence by punctuation
    splits = re.split(_SENTENCE_PUNCTUATION_R, text)
    for i, split in enumerate(splits):
        split = split.strip()  # Remove leading/trailing spaces
        if i % 2 == 1:  # Skip punctuation
            continue
        if not split:  # Skip empty lines
            continue
        if i == len(splits) - 1:  # Skip last line in case of missing punctuation
            if include_last:
                yield split + " "
        else:  # Add punctuation back
            yield split + splits[i + 1].strip() + " "


# TODO: Disable or lower profanity filter. The filter seems enabled by default, it replaces words like "holes in my roof" by "*** in my roof". This is not acceptable for a call center.
async def _handle_recognize_media(
    call: CallStateModel,
    client: CallAutomationClient,
    context: Optional[ContextEnum],
    style: MessageStyleEnum,
    text: Optional[str],
) -> None:
    """
    Play a media to a call participant and start recognizing the response.

    If `context` is provided, it will be used to track the operation.
    """
    logger.info(f"Recognizing voice: {text}")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.start_recognizing_media(
                end_silence_timeout=CONFIG.conversation.phone_silence_timeout_sec,
                input_type=RecognizeInputType.SPEECH,
                interrupt_prompt=True,
                operation_context=_context_builder({context}),
                play_prompt=(
                    _audio_from_text(
                        call=call,
                        style=style,
                        text=text,
                    )
                    if text
                    else None
                ),  # If no text is provided, only recognize
                speech_language=call.lang.short_code,
                target_participant=PhoneNumberIdentifier(call.initiate.phone_number),  # type: ignore
            )
    except ResourceNotFoundError:
        logger.debug(f"Call hung up before recognizing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug(f"Call hung up before playing")
        else:
            raise e


async def _handle_play_text(
    call: CallStateModel,
    client: CallAutomationClient,
    text: str,
    context: Optional[ContextEnum] = None,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
) -> None:
    """
    Play a text to a call participant.

    If `context` is provided, it will be used to track the operation.
    """
    logger.info(f"Playing text: {text}")
    try:
        assert call.voice_id, "Voice ID is required for playing text"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.play_media(
                operation_context=_context_builder({context}),
                play_source=_audio_from_text(
                    call=call,
                    style=style,
                    text=text,
                ),
            )
    except ResourceNotFoundError:
        logger.debug(f"Call hung up before playing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug(f"Call hung up before playing")
        else:
            raise e


async def handle_media(
    client: CallAutomationClient,
    call: CallStateModel,
    sound_url: str,
    context: Optional[ContextEnum] = None,
) -> None:
    """
    Play a media to a call participant.

    If `context` is provided, it will be used to track the operation.
    """
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.play_media(
                operation_context=_context_builder({context}),
                play_source=FileSource(url=sound_url),
            )
    except ResourceNotFoundError:
        logger.debug(f"Call hung up before playing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug(f"Call hung up before playing")
        else:
            raise e


async def handle_recognize_text(
    call: CallStateModel,
    client: CallAutomationClient,
    text: Optional[str],
    context: Optional[ContextEnum] = None,
    no_response_error: bool = False,
    store: bool = True,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
) -> None:
    """
    Play a text to a call participant and start recognizing the response.

    If `store` is `True`, the text will be stored in the call messages. Starts by playing text, then the "ready" sound, and finally starts recognizing the response.
    """
    if not text:  # Only recognize
        await _handle_recognize_media(
            call=call,
            client=client,
            context=context,
            style=style,
            text=None,
        )
        return

    chunks = await _chunk_before_tts(
        call=call,
        store=store,
        style=style,
        text=text,
    )
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:  # Last chunk
            if no_response_error:
                await _handle_recognize_media(
                    call=call,
                    client=client,
                    context=context,
                    style=style,
                    text=chunk,
                )
                return

        await _handle_play_text(
            call=call,
            client=client,
            context=context,
            style=style,
            text=chunk,
        )


async def handle_play_text(
    call: CallStateModel,
    client: CallAutomationClient,
    text: str,
    context: Optional[ContextEnum] = None,
    store: bool = True,
    style: MessageStyleEnum = MessageStyleEnum.NONE,
) -> None:
    """
    Play a text to a call participant.

    If `store` is `True`, the text will be stored in the call messages.
    """
    chunks = await _chunk_before_tts(
        call=call,
        store=store,
        style=style,
        text=text,
    )
    for chunk in chunks:
        await _handle_play_text(
            call=call,
            client=client,
            context=context,
            style=style,
            text=chunk,
        )


async def handle_clear_queue(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    """
    Clear the media queue of a call.
    """
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.cancel_all_media_operations()
    except ResourceNotFoundError:
        logger.debug(f"Call hung up before playing")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug(f"Call hung up before playing")
        else:
            raise e


async def _chunk_before_tts(
    call: CallStateModel,
    style: MessageStyleEnum,
    text: str,
    store: bool = True,
) -> list[str]:
    """
    Split a text in chunks and store them in the call messages.
    """
    # Sanitize text for TTS
    text = re.sub(_TTS_SANITIZER_R, "", text)

    # Store text in call messages
    if store:
        if (
            call.messages
            and call.messages[-1].persona == MessagePersonaEnum.ASSISTANT
            and call.messages[-1].style == style
        ):  # Append to last message if possible
            call.messages[-1].content += f" {text}"
        else:
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

    return chunks


def _audio_from_text(
    call: CallStateModel,
    style: MessageStyleEnum,
    text: str,
) -> SsmlSource:
    """
    Generate an audio source that can be read by Azure Communication Services SDK.

    Text requires to be SVG escaped, and SSML tags are used to control the voice. Plus, text is slowed down by 5% to make it more understandable for elderly people. Text is also truncated to 400 characters, as this is the limit of Azure Communication Services TTS, but a warning is logged.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-structure
    """
    # Azure Speech Service TTS limit is 400 characters
    if len(text) > 400:
        logger.warning(
            f"Text is too long to be processed by TTS, truncating to 400 characters, fix this!"
        )
        text = text[:400]
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
    return SsmlSource(ssml_text=ssml.strip())


async def handle_recognize_ivr(
    call: CallStateModel,
    choices: list[RecognitionChoice],
    client: CallAutomationClient,
    text: str,
    context: Optional[ContextEnum] = None,
) -> None:
    """
    Recognize an IVR response after playing a text.

    Starts by playing text, then starts recognizing the response. The recognition will be interrupted by the user if they start speaking. The recognition will be played in the call language.
    """
    logger.info(f"Recognizing IVR: {text}")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.start_recognizing_media(
                choices=choices,
                input_type=RecognizeInputType.CHOICES,
                interrupt_prompt=True,
                operation_context=_context_builder({context}),
                play_prompt=_audio_from_text(
                    call=call,
                    style=MessageStyleEnum.NONE,
                    text=text,
                ),
                speech_language=call.lang.short_code,
                target_participant=PhoneNumberIdentifier(call.initiate.phone_number),  # type: ignore
            )
    except ResourceNotFoundError:
        logger.debug(f"Call hung up before recognizing")


async def handle_hangup(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    logger.info(f"Hanging up: {call.initiate.phone_number}")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.hang_up(is_for_everyone=True)
    except ResourceNotFoundError:
        logger.debug("Call already hung up")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug("Call hung up before playing")
        else:
            raise e


async def handle_transfer(
    client: CallAutomationClient,
    call: CallStateModel,
    target: str,
    context: Optional[ContextEnum] = None,
) -> None:
    logger.info(f"Transferring call: {target}")
    try:
        assert call.voice_id, "Voice ID is required for recognizing media"
        async with _use_call_client(client, call.voice_id) as call_client:
            await call_client.transfer_call_to_participant(
                operation_context=_context_builder({context}),
                target_participant=PhoneNumberIdentifier(target),
            )
    except ResourceNotFoundError:
        logger.debug(f"Call hung up before transferring")
    except HttpResponseError as e:
        if "call already terminated" in e.message.lower():
            logger.debug(f"Call hung up before transferring")
        else:
            raise e


def _context_builder(contexts: Optional[set[Optional[ContextEnum]]]) -> Optional[str]:
    if not contexts:
        return None
    return json.dumps([context.value for context in contexts if context])


@asynccontextmanager
async def _use_call_client(
    client: CallAutomationClient, voice_id: str
) -> AsyncGenerator[CallConnectionClient, None]:
    yield client.get_call_connection(call_connection_id=voice_id)
