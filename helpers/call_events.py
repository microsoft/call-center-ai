from typing import Optional
from azure.communication.callautomation import (
    CallAutomationClient,
    DtmfTone,
    RecognitionChoice,
)
from helpers.config import CONFIG
from helpers.logging import build_logger, TRACER
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
    handle_recognize_ivr,
    handle_recognize_text,
    handle_transfer,
)
from fastapi import BackgroundTasks
import asyncio
from models.next import NextModel
from helpers.call_llm import llm_completion, llm_model, load_llm_chat


_logger = build_logger(__name__)
_sms = CONFIG.sms.instance()
_db = CONFIG.database.instance()


@TRACER.start_as_current_span("on_new_call")
async def on_new_call(
    callback_url: str,
    client: CallAutomationClient,
    incoming_context: str,
    phone_number: str,
) -> bool:
    _logger.debug(f"Incoming call handler caller ID: {phone_number}")

    try:
        answer_call_result = client.answer_call(
            callback_url=callback_url,
            cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
            incoming_call_context=incoming_context,
        )
        _logger.info(
            f"Answered call with {phone_number} ({answer_call_result.call_connection_id})"
        )
        return True

    except ClientAuthenticationError as e:
        _logger.error(
            "Authentication error with Communication Services, check the credentials",
            exc_info=True,
        )

    except HttpResponseError as e:
        if "lifetime validation of the signed http request failed" in e.message.lower():
            _logger.debug("Old call event received, ignoring")
        else:
            _logger.error(
                f"Unknown error answering call with {phone_number}", exc_info=True
            )

    return False


@TRACER.start_as_current_span("on_call_connected")
async def on_call_connected(
    call: CallStateModel,
    client: CallAutomationClient,
) -> None:
    _logger.info("Call connected")
    call.voice_recognition_retry = 0  # Reset recognition retry counter
    call.messages.append(
        MessageModel(
            action=MessageActionEnum.CALL,
            content="",
            persona=MessagePersonaEnum.HUMAN,
        )
    )
    await _db.call_aset(
        call
    )  # Save ASAP in DB allowing SMS answers to be more "in-sync"
    await _handle_ivr_language(
        call=call, client=client
    )  # Every time a call is answered, confirm the language


@TRACER.start_as_current_span("on_call_disconnected")
async def on_call_disconnected(
    background_tasks: BackgroundTasks,
    call: CallStateModel,
    client: CallAutomationClient,
) -> None:
    _logger.info("Call disconnected")
    await _handle_hangup(background_tasks, client, call)


@TRACER.start_as_current_span("on_speech_recognized")
async def on_speech_recognized(
    background_tasks: BackgroundTasks,
    call: CallStateModel,
    client: CallAutomationClient,
    text: str,
) -> None:
    _logger.info(f"Voice recognition: {text}")
    call.messages.append(MessageModel(content=text, persona=MessagePersonaEnum.HUMAN))
    await _db.call_aset(
        call
    )  # Save ASAP in DB allowing SMS answers to be more "in-sync"
    await handle_clear_queue(
        call=call, client=client
    )  # When the user speak, the conversation should continue based on its last message
    call = await load_llm_chat(
        background_tasks=background_tasks,
        call=call,
        client=client,
        post_call_intelligence=_post_call_intelligence,
    )


@TRACER.start_as_current_span("on_speech_timeout_error")
async def on_speech_timeout_error(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: Optional[list[CallContextEnum]],
) -> None:
    if not (contexts and CallContextEnum.LAST_CHUNK in contexts):
        _logger.debug("Ignoring timeout if bot is still speaking")
        return

    res_context = None
    if call.voice_recognition_retry < 10:
        call.voice_recognition_retry += 1
        trigger_timeout = True  # Should re-trigger an error or the LLM
        text = await CONFIG.prompts.tts.timeout_silence(call)
    else:
        trigger_timeout = False  # Shouldn't trigger anything, as call is ending
        res_context = CallContextEnum.GOODBYE
        text = await CONFIG.prompts.tts.goodbye(call)

    await handle_recognize_text(
        call=call,
        client=client,
        context=res_context,
        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
        text=text,
        trigger_timeout=trigger_timeout,
    )


@TRACER.start_as_current_span("on_speech_unknown_error")
async def on_speech_unknown_error(
    call: CallStateModel,
    client: CallAutomationClient,
    error_code: int,
) -> None:
    if error_code == 8511:  # Failure while trying to play the prompt
        _logger.warning("Failed to play prompt")
    else:
        _logger.warning(
            f"Recognition failed with unknown error code {error_code}, answering with default error"
        )
    await handle_recognize_text(
        call=call,
        client=client,
        store=False,  # Do not store error prompt as it perturbs the LLM and makes it hallucinate
        text=await CONFIG.prompts.tts.error(call),
    )


@TRACER.start_as_current_span("on_play_completed")
async def on_play_completed(
    background_tasks: BackgroundTasks,
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: Optional[list[CallContextEnum]],
) -> None:
    _logger.debug("Play completed")

    if not contexts:
        return

    if (
        CallContextEnum.TRANSFER_FAILED in contexts
        or CallContextEnum.GOODBYE in contexts
    ):  # Call ended
        _logger.info("Ending call")
        await _handle_hangup(background_tasks, client, call)

    elif CallContextEnum.CONNECT_AGENT in contexts:  # Call transfer
        _logger.info("Initiating transfer call initiated")
        await handle_transfer(
            call=call,
            client=client,
            target=call.initiate.agent_phone_number,
        )


@TRACER.start_as_current_span("on_play_error")
async def on_play_error(error_code: int) -> None:
    _logger.debug("Play failed")
    # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/play-action.md
    if error_code == 8535:  # Action failed, file format
        _logger.warning("Error during media play, file format is invalid")
    elif error_code == 8536:  # Action failed, file downloaded
        _logger.warning("Error during media play, file could not be downloaded")
    elif error_code == 8565:  # Action failed, AI services config
        _logger.error(
            "Error during media play, impossible to connect with Azure AI services"
        )
    elif error_code == 9999:  # Unknown
        _logger.warning("Error during media play, unknown internal server error")
    else:
        _logger.warning(f"Error during media play, unknown error code {error_code}")


@TRACER.start_as_current_span("on_ivr_recognized")
async def on_ivr_recognized(
    client: CallAutomationClient,
    call: CallStateModel,
    label: str,
    background_tasks: BackgroundTasks,
) -> None:
    try:
        lang = next(
            (x for x in call.initiate.lang.availables if x.short_code == label),
            call.initiate.lang.default_lang,
        )
    except ValueError:
        _logger.warning(f"Unknown IVR {label}, code not implemented")
        return

    _logger.info(f"Setting call language to {lang}")
    call.lang = lang.short_code
    await _db.call_aset(
        call
    )  # Persist language change, if the user calls back before the first message, the language will be set

    if len(call.messages) <= 1:  # First call, or only the call action
        await handle_recognize_text(
            call=call,
            client=client,
            text=await CONFIG.prompts.tts.hello(call),
        )

    else:  # Returning call
        await handle_recognize_text(
            call=call,
            client=client,
            style=MessageStyleEnum.CHEERFUL,
            text=await CONFIG.prompts.tts.welcome_back(call),
            trigger_timeout=False,  # Do not trigger timeout, as the chat will continue
        )
        call = await load_llm_chat(
            background_tasks=background_tasks,
            call=call,
            client=client,
            post_call_intelligence=_post_call_intelligence,
        )


@TRACER.start_as_current_span("on_transfer_completed")
async def on_transfer_completed() -> None:
    _logger.info("Call transfer accepted event")
    # TODO: Is there anything to do here?


@TRACER.start_as_current_span("on_transfer_error")
async def on_transfer_error(
    call: CallStateModel,
    client: CallAutomationClient,
    error_code: int,
) -> None:
    _logger.info(f"Error during call transfer, subCode {error_code}")
    await handle_recognize_text(
        call=call,
        client=client,
        context=CallContextEnum.TRANSFER_FAILED,
        text=await CONFIG.prompts.tts.calltransfer_failure(call),
    )


@TRACER.start_as_current_span("on_sms_received")
async def on_sms_received(
    background_tasks: BackgroundTasks,
    call: CallStateModel,
    client: CallAutomationClient,
    message: str,
) -> bool:
    _logger.info(f"SMS received from {call.initiate.phone_number}: {message}")
    call.messages.append(
        MessageModel(
            action=MessageActionEnum.SMS,
            content=message,
            persona=MessagePersonaEnum.HUMAN,
        )
    )
    await _db.call_aset(
        call
    )  # Save ASAP in DB allowing SMS answers to be more "in-sync"
    if not call.in_progress:
        _logger.info("Call not in progress, answering with SMS")
    else:
        _logger.info("Call in progress, answering with voice")
        await load_llm_chat(
            background_tasks=background_tasks,
            call=call,
            client=client,
            post_call_intelligence=_post_call_intelligence,
        )
    return True


async def _handle_hangup(
    background_tasks: BackgroundTasks,
    client: CallAutomationClient,
    call: CallStateModel,
) -> None:
    await handle_hangup(client=client, call=call)
    call.messages.append(
        MessageModel(
            content="",
            persona=MessagePersonaEnum.HUMAN,
            action=MessageActionEnum.HANGUP,
        )
    )
    _post_call_intelligence(call, background_tasks)


def _post_call_intelligence(
    call: CallStateModel, background_tasks: BackgroundTasks
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
        _logger.info(
            "Call ended before any interaction, skipping post-call intelligence"
        )
        return
    background_tasks.add_task(_post_call_next, call)
    background_tasks.add_task(_post_call_sms, call)
    background_tasks.add_task(_post_call_synthesis, call)


async def _post_call_sms(call: CallStateModel) -> None:
    """
    Send an SMS report to the customer.
    """
    content = await llm_completion(
        text=CONFIG.prompts.llm.sms_summary_system(call),
        call=call,
    )

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    _, content = extract_message_style(remove_message_action(content or ""))

    if not content:
        _logger.warning("Error generating SMS report")
        return
    _logger.info(f"SMS report: {content}")

    # Send the SMS to both the current caller and the policyholder
    success = False
    for number in set(
        [call.initiate.phone_number, call.claim.get("policyholder_phone", None)]
    ):
        if not number:
            continue
        res = await _sms.asend(content, number)
        if not res:
            _logger.warning(f"Failed sending SMS report to {number}")
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


async def _post_call_synthesis(call: CallStateModel) -> None:
    """
    Synthesize the call and store it to the model.
    """
    _logger.debug("Synthesizing call")

    short, long = await asyncio.gather(
        llm_completion(
            call=call,
            text=CONFIG.prompts.llm.synthesis_short_system(call),
        ),
        llm_completion(
            call=call,
            text=CONFIG.prompts.llm.citations_system(
                call=call,
                text=await llm_completion(
                    call=call,
                    text=CONFIG.prompts.llm.synthesis_long_system(call),
                ),
            ),
        ),
    )

    # Delete action and style from the message as they are in the history and LLM hallucinates them
    _, short = extract_message_style(remove_message_action(short or ""))
    _, long = extract_message_style(remove_message_action(long or ""))

    if not short or not long:
        _logger.warning("Error generating synthesis")
        return

    _logger.info(f"Short synthesis: {short}")
    _logger.info(f"Long synthesis: {long}")

    call.synthesis = SynthesisModel(
        long=long,
        short=short,
    )
    await _db.call_aset(call)


async def _post_call_next(call: CallStateModel) -> None:
    """
    Generate next action for the call.
    """
    next = await llm_model(
        call=call,
        model=NextModel,
        text=CONFIG.prompts.llm.next_system(call),
    )

    if not next:
        _logger.warning("Error generating next action")
        return

    _logger.info(f"Next action: {next}")
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
    for i, lang in enumerate(CONFIG.workflow.initiate.lang.availables):
        choices.append(
            RecognitionChoice(
                label=lang.short_code,
                phrases=lang.pronunciations_en,
                tone=tones[i],
            )
        )
    await handle_recognize_ivr(
        call=call,
        client=client,
        choices=choices,
        text=await CONFIG.prompts.tts.ivr_language(call),
    )
