import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiojobs import Scheduler
from azure.cognitiveservices.speech import (
    SpeechSynthesizer,
)
from azure.communication.callautomation import (
    AzureBlobContainerRecordingStorage,
    DtmfTone,
    MediaStreamingAudioChannelType,
    MediaStreamingContentType,
    MediaStreamingOptions,
    MediaStreamingTransportType,
    RecognitionChoice,
    RecordingChannel,
    RecordingContent,
    RecordingFormat,
)
from azure.communication.callautomation.aio import CallAutomationClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from pydantic import ValidationError

from app.helpers.call_llm import load_llm_chat
from app.helpers.call_utils import (
    ContextEnum as CallContextEnum,
    handle_automation_tts,
    handle_hangup,
    handle_realtime_tts,
    handle_recognize_ivr,
    start_audio_streaming,
)
from app.helpers.config import CONFIG
from app.helpers.features import recognition_retry_max, recording_enabled
from app.helpers.llm_worker import completion_sync
from app.helpers.logging import logger
from app.helpers.monitoring import SpanAttributeEnum, start_as_current_span
from app.models.call import CallStateModel
from app.models.message import (
    ActionEnum as MessageActionEnum,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    extract_message_style,
)
from app.models.next import NextModel
from app.models.synthesis import SynthesisModel

_sms = CONFIG.sms.instance
_db = CONFIG.database.instance


@start_as_current_span("on_new_call")
async def on_new_call(
    callback_url: str,
    client: CallAutomationClient,
    incoming_context: str,
    phone_number: str,
    wss_url: str,
) -> bool:
    """
    Callback for when a new call is received.

    Answers the call and starts the media streaming.
    """
    logger.debug("Incoming call handler")

    streaming_options = MediaStreamingOptions(
        audio_channel_type=MediaStreamingAudioChannelType.UNMIXED,
        content_type=MediaStreamingContentType.AUDIO,
        enable_bidirectional=True,
        start_media_streaming=False,
        transport_type=MediaStreamingTransportType.WEBSOCKET,
        transport_url=wss_url,
    )

    try:
        answer_call_result = await client.answer_call(
            callback_url=callback_url,
            cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
            incoming_call_context=incoming_context,
            media_streaming=streaming_options,
        )
        logger.info("Answered call (%s)", answer_call_result.call_connection_id)
        return True

    except ClientAuthenticationError:
        logger.exception(
            "Authentication error with Communication Services, check the credentials"
        )

    except HttpResponseError as e:
        if "lifetime validation of the signed http request failed" in e.message.lower():
            logger.debug("Old call event received, ignoring")
        else:
            logger.exception(
                "Unknown error answering call with %s",
                phone_number,
            )

    return False


@start_as_current_span("on_call_connected")
async def on_call_connected(
    call: CallStateModel,
    client: CallAutomationClient,
    scheduler: Scheduler,
    server_call_id: str,
) -> None:
    """
    Callback for when the call is connected.

    Ask for the language and start recording the call.
    """
    logger.info("Call connected, asking for language")

    # Execute business logic
    await asyncio.gather(
        _handle_ivr_language(
            call=call,
            client=client,
            scheduler=scheduler,
        ),  # First, every time a call is answered, confirm the language
        _handle_recording(
            call=call,
            client=client,
            server_call_id=server_call_id,
        ),  # Second, start recording the call
    )

    # Add define the call as in progress
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.in_progress = True
        call.recognition_retry = 0
        call.messages.append(
            MessageModel(
                action=MessageActionEnum.CALL,
                content="",
                persona=MessagePersonaEnum.HUMAN,
            )
        )


@start_as_current_span("on_call_disconnected")
async def on_call_disconnected(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
) -> None:
    """
    Callback for when the call is disconnected.

    Hangs up the call and stores the final message.
    """
    logger.info("Call disconnected")
    await hangup_now(
        call=call,
        client=client,
        post_callback=post_callback,
        scheduler=scheduler,
    )


@start_as_current_span("on_audio_connected")
async def on_audio_connected(  # noqa: PLR0913
    audio_in: asyncio.Queue[bytes],
    audio_out: asyncio.Queue[bytes | bool],
    audio_sample_rate: int,
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    training_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
) -> None:
    """
    Callback for when the audio stream is connected.

    Starts the real-time conversation with the LLM.
    """
    await load_llm_chat(
        audio_in=audio_in,
        audio_out=audio_out,
        audio_sample_rate=audio_sample_rate,
        automation_client=client,
        call=call,
        post_callback=post_callback,
        scheduler=scheduler,
        training_callback=training_callback,
    )


@start_as_current_span("on_automation_recognize_error")
async def on_automation_recognize_error(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: set[CallContextEnum] | None,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
) -> None:
    """
    Callback for when an automation recognition fails.

    If the call should continue, increments the recognition retry counter and plays a timeout prompt. Else, hangs up the call.
    """
    if not await _pre_recognize_error(
        call=call,
        scheduler=scheduler,
    ):
        # Play TTS
        await handle_automation_tts(
            call=call,
            client=client,
            context=CallContextEnum.GOODBYE,
            post_callback=post_callback,
            scheduler=scheduler,
            store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
            text=await CONFIG.prompts.tts.goodbye(call),
        )
        return

    # There are no retries with Call Automation SDK out of IVR recognition, as voice in-call is managed with raw audio in the LLM stack
    if not contexts or CallContextEnum.IVR_LANG_SELECT not in contexts:
        logger.warning("Unknown context %s, no action taken", contexts)

    # Enrich span
    SpanAttributeEnum.CALL_CHANNEL.attribute("ivr")

    # Retry IVR recognition
    logger.info(
        "Timeout, retrying language selection (%s/%s)",
        call.recognition_retry,
        await recognition_retry_max(),
    )
    await _handle_ivr_language(
        call=call,
        client=client,
        scheduler=scheduler,
    )


@start_as_current_span("on_realtime_recognize_error")
async def on_realtime_recognize_error(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
    tts_client: SpeechSynthesizer,
) -> None:
    """
    Callback for when a real-time recognition fails.

    If the call should continue, increments the recognition retry counter and plays a timeout prompt. Else, hangs up the call.
    """
    if not await _pre_recognize_error(
        call=call,
        scheduler=scheduler,
    ):
        await hangup_realtime_now(
            call=call,
            client=client,
            post_callback=post_callback,
            scheduler=scheduler,
            tts_client=tts_client,
        )
        return

    # Play a timeout prompt
    await handle_realtime_tts(
        call=call,
        scheduler=scheduler,
        store=False,
        text=await CONFIG.prompts.tts.timeout_silence(call),
        tts_client=tts_client,
    )


async def hangup_realtime_now(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
    tts_client: SpeechSynthesizer,
) -> None:
    """
    Hangup the call and play a goodbye.
    """
    # Play TTS
    await handle_realtime_tts(
        call=call,
        scheduler=scheduler,
        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
        text=await CONFIG.prompts.tts.goodbye(call),
        tts_client=tts_client,
    )
    # TODO: Hack to avoid the call to close before TTS is played
    await asyncio.sleep(10)
    # Hangup
    await hangup_now(
        call=call,
        client=client,
        post_callback=post_callback,
        scheduler=scheduler,
    )


async def _pre_recognize_error(
    call: CallStateModel,
    scheduler: Scheduler,
) -> bool:
    """
    Handle pre-recognition errors.

    Tests if the call should continue after a recognition error. If yes, increments the recognition retry counter.

    Returns True if the call should continue, False if it should end.
    """
    # Voice retries are exhausted, end call
    if call.recognition_retry >= await recognition_retry_max():
        logger.info("Timeout, ending call")
        return False

    # Increment the recognition retry counter
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.recognition_retry += 1

    return True


@start_as_current_span("on_play_started")
async def on_play_started(
    call: CallStateModel,
    scheduler: Scheduler,
) -> None:
    """
    Callback for when a media play action starts.

    Updates the last interaction time.
    """
    logger.debug("Play started")

    # Enrich span
    SpanAttributeEnum.CALL_CHANNEL.attribute("voice")

    # Update last interaction
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.last_interaction_at = datetime.now(UTC)


@start_as_current_span("on_play_completed")
async def on_automation_play_completed(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: set[CallContextEnum] | None,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
) -> None:
    """
    Callback for when a media play action completes.

    If the call should continue, updates the last interaction time. Else, hangs up the call.
    """
    logger.debug("Play completed")

    # Enrich span
    SpanAttributeEnum.CALL_CHANNEL.attribute("voice")

    # Update last interaction
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.last_interaction_at = datetime.now(UTC)

    # Skip if no context data
    if not contexts:
        return

    # Call ended context
    if (
        CallContextEnum.TRANSFER_FAILED in contexts
        or CallContextEnum.GOODBYE in contexts
    ):
        logger.info("Ending call")
        await hangup_now(
            call=call,
            client=client,
            post_callback=post_callback,
            scheduler=scheduler,
        )
        return

    logger.warning("Unknown context %s", contexts)


@start_as_current_span("on_play_error")
async def on_play_error(error_code: int) -> None:
    """
    Callback for when a media play action fails.

    Logs the error and suppresses known errors from the Communication Services SDK.
    """
    logger.debug("Play failed")

    # Enrich span
    SpanAttributeEnum.CALL_CHANNEL.attribute("voice")

    # Suppress known errors
    # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/play-action.md
    match error_code:
        case 8535:
            # Action failed, file format
            logger.warning("Error during media play, file format is invalid")
        case 8536:
            # Action failed, file downloaded
            logger.warning("Error during media play, file could not be downloaded")
        case 8565:
            # Action failed, AI services config
            logger.error(
                "Error during media play, impossible to connect with Azure AI services"
            )
        case 9999:
            # Unknown error code
            logger.warning("Error during media play, unknown internal server error")
        case _:
            logger.warning("Error during media play, unknown error code %s", error_code)


@start_as_current_span("on_ivr_recognized")
async def on_ivr_recognized(
    call: CallStateModel,
    client: CallAutomationClient,
    label: str,
    scheduler: Scheduler,
) -> None:
    """
    Callback for when an IVR recognition is successful.

    Updates the call language and starts the real-time conversation with the LLM.
    """
    logger.info("IVR recognized: %s", label)

    # Enrich span
    SpanAttributeEnum.CALL_CHANNEL.attribute("ivr")
    SpanAttributeEnum.CALL_MESSAGE.attribute(label)

    # Parse language from label
    try:
        lang = next(
            (x for x in call.initiate.lang.availables if x.short_code == label),
            call.initiate.lang.default_lang,
        )
    except ValueError:
        logger.warning("Unknown IVR %s, code not implemented", label)
        return

    logger.info("Setting call language to %s", lang)
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.lang_short_code = lang.short_code
        call.recognition_retry = 0

    await start_audio_streaming(
        call=call,
        client=client,
    )


@start_as_current_span("on_transfer_error")
async def on_transfer_error(
    call: CallStateModel,
    client: CallAutomationClient,
    error_code: int,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
) -> None:
    """
    Callback for when a call transfer fails.

    Logs the error and plays a TTS message to the user.
    """
    logger.info("Error during call transfer, subCode %s", error_code)
    await handle_automation_tts(
        call=call,
        client=client,
        context=CallContextEnum.TRANSFER_FAILED,
        post_callback=post_callback,
        scheduler=scheduler,
        text=await CONFIG.prompts.tts.calltransfer_failure(call),
    )


@start_as_current_span("on_sms_received")
async def on_sms_received(
    call: CallStateModel,
    message: str,
    scheduler: Scheduler,
) -> bool:
    """
    Callback for when an SMS is received.

    Adds the SMS to the call history and answers with voice if the call is in progress. If not, answers with SMS.
    """
    logger.info("SMS received from %s: %s", call.initiate.phone_number, message)

    # Enrich span
    SpanAttributeEnum.CALL_CHANNEL.attribute("sms")
    SpanAttributeEnum.CALL_MESSAGE.attribute(message)

    # Add the SMS to the call history
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.messages.append(
            MessageModel(
                action=MessageActionEnum.SMS,
                content=message,
                lang_short_code=call.lang.short_code,
                persona=MessagePersonaEnum.HUMAN,
            )
        )

    # If the call is not in progress, answer with SMS
    if not call.in_progress:
        logger.info("Call not in progress, answering with SMS")

    # If the call is in progress, answer with voice
    else:
        logger.info("Call in progress, answering with voice")
        # TODO: Reimplement SMS answers in voice
        # await load_llm_chat(
        #     call=call,
        #     client=client,
        #     post_callback=post_callback,
        # )

    return True


async def hangup_now(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    scheduler: Scheduler,
) -> None:
    """
    Hangup the call and store the final message.
    """

    async def _store(call: CallStateModel) -> None:
        async with _db.call_transac(
            call=call,
            scheduler=scheduler,
        ):
            call.in_progress = False
            call.messages.append(
                MessageModel(
                    action=MessageActionEnum.HANGUP,
                    content="",
                    persona=MessagePersonaEnum.HUMAN,
                )
            )

    await asyncio.gather(
        handle_hangup(client=client, call=call),
        _store(call),
        post_callback(call),
    )


async def on_end_call(
    call: CallStateModel,
    scheduler: Scheduler,
) -> None:
    """
    Callback for when a call ends.

    Generates post-call intelligence if the call had interactions.
    """
    if not call.had_interaction():
        logger.info(
            "Call ended before any interaction, skipping post-call intelligence"
        )
        return

    await asyncio.gather(
        _intelligence_next(
            call=call,
            scheduler=scheduler,
        ),
        _intelligence_sms(
            call=call,
            scheduler=scheduler,
        ),
        _intelligence_synthesis(
            call=call,
            scheduler=scheduler,
        ),
    )


async def _intelligence_sms(
    call: CallStateModel,
    scheduler: Scheduler,
) -> None:
    """
    Send an SMS report to the customer.
    """

    def _validate(req: str | None) -> tuple[bool, str | None, str | None]:
        if not req:
            return False, "No SMS content", None
        return True, None, req

    content = await completion_sync(
        res_type=str,
        system=CONFIG.prompts.llm.sms_summary_system(call),
        validation_callback=_validate,
    )

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    _, content = extract_message_style(content or "")

    if not content:
        logger.warning("Error generating SMS report")
        return

    # Send the SMS to both the current caller and the policyholder
    success = False
    for number in set(
        [call.initiate.phone_number, call.claim.get("policyholder_phone", None)]
    ):
        if not number:
            continue
        res = await _sms.send(content, number)
        if not res:
            logger.warning("Failed sending SMS report to %s", number)
            continue
        success = True

    if success:
        async with _db.call_transac(
            call=call,
            scheduler=scheduler,
        ):
            # Dont't store the lang as we aren't sure about the language of the SMS
            call.messages.append(
                MessageModel(
                    action=MessageActionEnum.SMS,
                    content=content,
                    persona=MessagePersonaEnum.ASSISTANT,
                )
            )


async def _intelligence_synthesis(
    call: CallStateModel,
    scheduler: Scheduler,
) -> None:
    """
    Synthesize the call and store it to the model.
    """
    logger.debug("Synthesizing call")

    def _validate(
        req: str | None,
    ) -> tuple[bool, str | None, SynthesisModel | None]:
        if not req:
            return False, "Empty response", None
        try:
            return True, None, SynthesisModel.model_validate_json(req)
        except ValidationError as e:
            return False, str(e), None

    model = await completion_sync(
        res_type=SynthesisModel,
        system=CONFIG.prompts.llm.synthesis_system(call),
        validate_json=True,
        validation_callback=_validate,
    )
    if not model:
        logger.warning("Error generating synthesis")
        return

    logger.info("Synthesis: %s", model)
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.synthesis = model


async def _intelligence_next(
    call: CallStateModel,
    scheduler: Scheduler,
) -> None:
    """
    Generate next action for the call.
    """
    logger.debug("Generating next action")

    def _validate(
        req: str | None,
    ) -> tuple[bool, str | None, NextModel | None]:
        if not req:
            return False, "Empty response", None
        try:
            return True, None, NextModel.model_validate_json(req)
        except ValidationError as e:
            return False, str(e), None

    model = await completion_sync(
        res_type=NextModel,
        system=CONFIG.prompts.llm.next_system(call),
        validate_json=True,
        validation_callback=_validate,
    )
    if not model:
        logger.warning("Error generating next action")
        return

    logger.info("Next action: %s", model)
    async with _db.call_transac(
        call=call,
        scheduler=scheduler,
    ):
        call.next = model


async def _handle_ivr_language(
    call: CallStateModel,
    client: CallAutomationClient,
    scheduler: Scheduler,
) -> None:
    """
    Handle IVR language selection.

    If only one language is available, selects it by default. Else, plays the IVR prompt.
    """
    # If only one language is available, skip the IVR
    if len(call.initiate.lang.availables) == 1:
        short_code = call.initiate.lang.availables[0].short_code
        logger.info("Only one language available, selecting %s by default", short_code)
        await on_ivr_recognized(
            call=call,
            client=client,
            label=short_code,
            scheduler=scheduler,
        )
        return

    tones = [
        DtmfTone.ONE,
        DtmfTone.TWO,
        DtmfTone.THREE,
        DtmfTone.FOUR,
        DtmfTone.FIVE,
        DtmfTone.SIX,
        DtmfTone.SEVEN,
        DtmfTone.EIGHT,
        DtmfTone.NINE,
    ]
    choices = []
    for i, lang in enumerate(call.initiate.lang.availables):
        choices.append(
            RecognitionChoice(
                label=lang.short_code,
                phrases=lang.pronunciations_en,
                tone=tones[i],
            )
        )
    await handle_recognize_ivr(
        call=call,
        choices=choices,
        client=client,
        context=CallContextEnum.IVR_LANG_SELECT,
        text=await CONFIG.prompts.tts.ivr_language(call),
    )


async def _handle_recording(
    call: CallStateModel,
    client: CallAutomationClient,
    server_call_id: str,
) -> None:
    """
    Start recording the call.

    Feature activation is checked before starting the recording.
    """
    if not await recording_enabled():
        return

    assert CONFIG.communication_services.recording_container_url
    recording = await client.start_recording(
        recording_channel_type=RecordingChannel.UNMIXED,
        recording_content_type=RecordingContent.AUDIO,
        recording_format_type=RecordingFormat.WAV,
        server_call_id=server_call_id,
        recording_storage=AzureBlobContainerRecordingStorage(
            CONFIG.communication_services.recording_container_url
        ),
    )
    logger.info(
        "Recording started for %s (%s)",
        call.initiate.phone_number,
        recording.recording_id,
    )
