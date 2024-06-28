from azure.communication.callautomation import DtmfTone, RecognitionChoice
from azure.communication.callautomation.aio import CallAutomationClient
from pydantic import ValidationError
from helpers.config import CONFIG
from helpers.logging import logger, tracer
from typing import Awaitable, Callable, Optional
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
)
from models.synthesis import SynthesisModel
from models.call import CallStateModel
from models.message import (
    ActionEnum as MessageActionEnum,
    extract_message_style,
    MessageModel,
    PersonaEnum as MessagePersonaEnum,
    remove_message_action,
    StyleEnum as MessageStyleEnum,
)
from helpers.call_utils import (
    ContextEnum as CallContextEnum,
    handle_clear_queue,
    handle_hangup,
    handle_play_text,
    handle_recognize_ivr,
    handle_recognize_text,
    handle_transfer,
)
from helpers.call_llm import load_llm_chat
from helpers.llm_worker import completion_sync
from models.next import NextModel
import asyncio


_sms = CONFIG.sms.instance()
_db = CONFIG.database.instance()


@tracer.start_as_current_span("on_new_call")
async def on_new_call(
    callback_url: str,
    client: CallAutomationClient,
    incoming_context: str,
    phone_number: str,
) -> bool:
    logger.debug(f"Incoming call handler caller ID: {phone_number}")

    try:
        answer_call_result = await client.answer_call(
            callback_url=callback_url,
            cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
            incoming_call_context=incoming_context,
        )
        logger.info(
            f"Answered call with {phone_number} ({answer_call_result.call_connection_id})"
        )
        return True

    except ClientAuthenticationError as e:
        logger.error(
            "Authentication error with Communication Services, check the credentials",
            exc_info=True,
        )

    except HttpResponseError as e:
        if "lifetime validation of the signed http request failed" in e.message.lower():
            logger.debug("Old call event received, ignoring")
        else:
            logger.error(
                f"Unknown error answering call with {phone_number}", exc_info=True
            )

    return False


@tracer.start_as_current_span("on_call_connected")
async def on_call_connected(
    call: CallStateModel,
    client: CallAutomationClient,
) -> None:
    logger.info("Call connected, asking for language")
    call.recognition_retry = 0  # Reset recognition retry counter
    call.messages.append(
        MessageModel(
            action=MessageActionEnum.CALL,
            content="",
            persona=MessagePersonaEnum.HUMAN,
        )
    )
    await asyncio.gather(
        _handle_ivr_language(
            call=call, client=client
        ),  # First, every time a call is answered, confirm the language
        _db.call_aset(
            call
        ),  # save in DB allowing SMS answers to be more "in-sync", should be quick enough to be in sync with the next message
    )


@tracer.start_as_current_span("on_call_disconnected")
async def on_call_disconnected(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    logger.info("Call disconnected")
    await _handle_hangup(
        call=call,
        client=client,
        post_callback=post_callback,
    )


@tracer.start_as_current_span("on_speech_recognized")
async def on_speech_recognized(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    text: str,
    trainings_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    logger.info(f"Voice recognition: {text}")
    call.messages.append(
        MessageModel(
            content=text,
            persona=MessagePersonaEnum.HUMAN,
        )
    )
    call.recognition_retry = 0  # Reset recognition retry counter
    await asyncio.gather(
        handle_clear_queue(
            call=call,
            client=client,
        ),  # First, when the user speak, the conversation should continue based on its last message
        load_llm_chat(
            call=call,
            client=client,
            post_callback=post_callback,
            trainings_callback=trainings_callback,
        ),  # Second, the LLM should be loaded to continue the conversation
        _db.call_aset(
            call
        ),  # Third, save in DB allowing SMS responses to be more "in-sync" if they are sent during the generation
    )  # All in parallel to lower the response latency


@tracer.start_as_current_span("on_recognize_timeout_error")
async def on_recognize_timeout_error(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: Optional[set[CallContextEnum]],
) -> None:
    if (
        contexts and CallContextEnum.IVR_LANG_SELECT in contexts
    ):  # Retry IVR recognition
        if call.recognition_retry < CONFIG.conversation.voice_recognition_retry_max:
            call.recognition_retry += 1
            logger.info(
                f"Timeout, retrying language selection ({call.recognition_retry}/{CONFIG.conversation.voice_recognition_retry_max})"
            )
            await _handle_ivr_language(call=call, client=client)
        else:  # IVR retries are exhausted, end call
            logger.info("Timeout, ending call")
            await _handle_goodbye(
                call=call,
                client=client,
            )
        return

    if (
        call.recognition_retry >= CONFIG.conversation.voice_recognition_retry_max
    ):  # Voice retries are exhausted, end call
        logger.info("Timeout, ending call")
        await _handle_goodbye(
            call=call,
            client=client,
        )
        return

    # Retry voice recognition
    call.recognition_retry += 1
    logger.info(
        f"Timeout, retrying voice recognition ({call.recognition_retry}/{CONFIG.conversation.voice_recognition_retry_max})"
    )
    await handle_recognize_text(
        call=call,
        client=client,
        no_response_error=True,
        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
        text=await CONFIG.prompts.tts.timeout_silence(call),
    )


async def _handle_goodbye(
    call: CallStateModel,
    client: CallAutomationClient,
) -> None:
    await handle_play_text(
        call=call,
        client=client,
        context=CallContextEnum.GOODBYE,
        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
        text=await CONFIG.prompts.tts.goodbye(call),
    )


@tracer.start_as_current_span("on_recognize_unknown_error")
async def on_recognize_unknown_error(
    call: CallStateModel,
    client: CallAutomationClient,
    error_code: int,
) -> None:
    if error_code == 8511:  # Failure while trying to play the prompt
        logger.warning("Failed to play prompt")
    else:
        logger.warning(
            f"Recognition failed with unknown error code {error_code}, answering with default error"
        )
    await handle_recognize_text(
        call=call,
        client=client,
        no_response_error=True,
        store=False,  # Do not store error prompt as it perturbs the LLM and makes it hallucinate
        text=await CONFIG.prompts.tts.error(call),
    )


@tracer.start_as_current_span("on_play_completed")
async def on_play_completed(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: Optional[set[CallContextEnum]],
    post_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    logger.debug("Play completed")

    if not contexts:
        return

    if (
        CallContextEnum.TRANSFER_FAILED in contexts
        or CallContextEnum.GOODBYE in contexts
    ):  # Call ended
        logger.info("Ending call")
        await _handle_hangup(
            call=call,
            client=client,
            post_callback=post_callback,
        )

    elif CallContextEnum.CONNECT_AGENT in contexts:  # Call transfer
        logger.info("Initiating transfer call initiated")
        await handle_transfer(
            call=call,
            client=client,
            target=call.initiate.agent_phone_number,
        )


@tracer.start_as_current_span("on_play_error")
async def on_play_error(error_code: int) -> None:
    logger.debug("Play failed")
    # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/play-action.md
    if error_code == 8535:  # Action failed, file format
        logger.warning("Error during media play, file format is invalid")
    elif error_code == 8536:  # Action failed, file downloaded
        logger.warning("Error during media play, file could not be downloaded")
    elif error_code == 8565:  # Action failed, AI services config
        logger.error(
            "Error during media play, impossible to connect with Azure AI services"
        )
    elif error_code == 9999:  # Unknown
        logger.warning("Error during media play, unknown internal server error")
    else:
        logger.warning(f"Error during media play, unknown error code {error_code}")


@tracer.start_as_current_span("on_ivr_recognized")
async def on_ivr_recognized(
    call: CallStateModel,
    client: CallAutomationClient,
    label: str,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    trainings_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    call.recognition_retry = 0  # Reset recognition retry counter
    try:
        lang = next(
            (x for x in call.initiate.lang.availables if x.short_code == label),
            call.initiate.lang.default_lang,
        )
    except ValueError:
        logger.warning(f"Unknown IVR {label}, code not implemented")
        return

    logger.info(f"Setting call language to {lang}")
    call.lang = lang.short_code
    persist_coro = _db.call_aset(call)

    if len(call.messages) <= 1:  # First call, or only the call action
        await asyncio.gather(
            handle_recognize_text(
                call=call,
                client=client,
                text=await CONFIG.prompts.tts.hello(call),
            ),  # First, greet the user
            persist_coro,  # Second, persist language change for next messages, should be quick enough to be in sync with the next message
            load_llm_chat(
                call=call,
                client=client,
                post_callback=post_callback,
                trainings_callback=trainings_callback,
            ),  # Third, the LLM should be loaded to continue the conversation
        )  # All in parallel to lower the response latency

    else:  # Returning call
        await asyncio.gather(
            handle_recognize_text(
                call=call,
                client=client,
                style=MessageStyleEnum.CHEERFUL,
                text=await CONFIG.prompts.tts.welcome_back(call),
            ),  # First, welcome back the user
            persist_coro,  # Second, persist language change for next messages, should be quick enough to be in sync with the next message
            load_llm_chat(
                call=call,
                client=client,
                post_callback=post_callback,
                trainings_callback=trainings_callback,
            ),  # Third, the LLM should be loaded to continue the conversation
        )


@tracer.start_as_current_span("on_transfer_completed")
async def on_transfer_completed() -> None:
    logger.info("Call transfer accepted event")
    # TODO: Is there anything to do here?


@tracer.start_as_current_span("on_transfer_error")
async def on_transfer_error(
    call: CallStateModel,
    client: CallAutomationClient,
    error_code: int,
) -> None:
    logger.info(f"Error during call transfer, subCode {error_code}")
    await handle_recognize_text(
        call=call,
        client=client,
        context=CallContextEnum.TRANSFER_FAILED,
        no_response_error=True,
        text=await CONFIG.prompts.tts.calltransfer_failure(call),
    )


@tracer.start_as_current_span("on_sms_received")
async def on_sms_received(
    call: CallStateModel,
    client: CallAutomationClient,
    message: str,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
    trainings_callback: Callable[[CallStateModel], Awaitable[None]],
) -> bool:
    logger.info(f"SMS received from {call.initiate.phone_number}: {message}")
    call.messages.append(
        MessageModel(
            action=MessageActionEnum.SMS,
            content=message,
            persona=MessagePersonaEnum.HUMAN,
        )
    )
    await _db.call_aset(call)  # save in DB allowing SMS answers to be more "in-sync"
    if not call.in_progress:
        logger.info("Call not in progress, answering with SMS")
    else:
        logger.info("Call in progress, answering with voice")
        await load_llm_chat(
            call=call,
            client=client,
            post_callback=post_callback,
            trainings_callback=trainings_callback,
        )
    return True


async def _handle_hangup(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], Awaitable[None]],
) -> None:
    await handle_hangup(client=client, call=call)
    call.messages.append(
        MessageModel(
            action=MessageActionEnum.HANGUP,
            content="",
            persona=MessagePersonaEnum.HUMAN,
        )
    )
    await post_callback(call)


async def on_end_call(
    call: CallStateModel,
) -> None:
    """
    Shortcut to run all post-call intelligence tasks in background.
    """
    if (
        len(call.messages) >= 3
        and call.messages[-3].action == MessageActionEnum.CALL
        and call.messages[-2].persona == MessagePersonaEnum.ASSISTANT
        and call.messages[-1].action == MessageActionEnum.HANGUP
    ):
        logger.info(
            "Call ended before any interaction, skipping post-call intelligence"
        )
        return

    await asyncio.gather(
        _intelligence_next(call),
        _intelligence_sms(call),
        _intelligence_synthesis(call),
    )


async def _intelligence_sms(call: CallStateModel) -> None:
    """
    Send an SMS report to the customer.
    """

    def _validate(req: Optional[str]) -> tuple[bool, Optional[str], Optional[str]]:
        if not req:
            return False, "No SMS content", None
        if len(req) < 10:
            return False, "SMS content too short", None
        return True, None, req

    content = await completion_sync(
        res_type=str,
        system=CONFIG.prompts.llm.sms_summary_system(call),
        validation_callback=_validate,
    )

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    _, content = extract_message_style(remove_message_action(content or ""))

    if not content:
        logger.warning("Error generating SMS report")
        return
    logger.info(f"SMS report: {content}")

    # Send the SMS to both the current caller and the policyholder
    success = False
    for number in set(
        [call.initiate.phone_number, call.claim.get("policyholder_phone", None)]
    ):
        if not number:
            continue
        res = await _sms.asend(content, number)
        if not res:
            logger.warning(f"Failed sending SMS report to {number}")
            continue
        success = True

    if success:
        call.messages.append(
            MessageModel(
                action=MessageActionEnum.SMS,
                content=content,
                persona=MessagePersonaEnum.ASSISTANT,
            )
        )
        await _db.call_aset(call)


async def _intelligence_synthesis(call: CallStateModel) -> None:
    """
    Synthesize the call and store it to the model.
    """
    logger.debug("Synthesizing call")

    def _validate(
        req: Optional[str],
    ) -> tuple[bool, Optional[str], Optional[SynthesisModel]]:
        if not req:
            return False, "Empty response", None
        try:
            return True, None, SynthesisModel.model_validate_json(req)
        except ValidationError as e:
            return False, str(e), None

    synthesis = await completion_sync(
        res_type=SynthesisModel,
        system=CONFIG.prompts.llm.synthesis_system(call),
        validate_json=True,
        validation_callback=_validate,
    )

    if not synthesis:
        logger.warning("Error generating synthesis")
        return

    logger.info(f"Synthesis: {synthesis}")
    call.synthesis = synthesis
    await _db.call_aset(call)


async def _intelligence_next(call: CallStateModel) -> None:
    """
    Generate next action for the call.
    """
    logger.debug("Generating next action")

    def _validate(
        req: Optional[str],
    ) -> tuple[bool, Optional[str], Optional[NextModel]]:
        if not req:
            return False, "Empty response", None
        try:
            return True, None, NextModel.model_validate_json(req)
        except ValidationError as e:
            return False, str(e), None

    next = await completion_sync(
        res_type=NextModel,
        system=CONFIG.prompts.llm.next_system(call),
        validate_json=True,
        validation_callback=_validate,
    )

    if not next:
        logger.warning("Error generating next action")
        return

    logger.info(f"Next action: {next}")
    call.next = next
    await _db.call_aset(call)


async def _handle_ivr_language(
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
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
    for i, lang in enumerate(CONFIG.conversation.initiate.lang.availables):
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
