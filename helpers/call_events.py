from azure.communication.callautomation import DtmfTone, RecognitionChoice
from azure.communication.callautomation.aio import CallAutomationClient
from helpers.config import CONFIG
from helpers.logging import logger, tracer
from typing import Callable, Optional
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
from helpers.call_llm import llm_completion, llm_model, load_llm_chat
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
    logger.info("Call connected")
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


@tracer.start_as_current_span("on_call_disconnected")
async def on_call_disconnected(
    call: CallStateModel,
    client: CallAutomationClient,
    post_callback: Callable[[CallStateModel], None],
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
    post_callback: Callable[[CallStateModel], None],
    text: str,
    trainings_callback: Callable[[CallStateModel], None],
) -> None:
    logger.info(f"Voice recognition: {text}")
    call.messages.append(MessageModel(content=text, persona=MessagePersonaEnum.HUMAN))
    await asyncio.gather(
        _db.call_aset(
            call
        ),  # First, save ASAP in DB allowing SMS answers to be more "in-sync"
        handle_clear_queue(
            call=call,
            client=client,
        ),  # Second, when the user speak, the conversation should continue based on its last message
        load_llm_chat(
            call=call,
            client=client,
            post_callback=post_callback,
            trainings_callback=trainings_callback,
        ),  # Third, the LLM should be loaded to continue the conversation
    )  # All in parallel to lower the answer latency


@tracer.start_as_current_span("on_speech_timeout_error")
async def on_speech_timeout_error(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: Optional[list[CallContextEnum]],
) -> None:
    if not (contexts and CallContextEnum.LAST_CHUNK in contexts):
        logger.debug("Ignoring timeout if bot is still speaking")
        return

    res_context = None
    if call.voice_recognition_retry < 10:
        call.voice_recognition_retry += 1
        timeout_error = True  # Should re-trigger an error or the LLM
        text = await CONFIG.prompts.tts.timeout_silence(call)
    else:
        timeout_error = False  # Shouldn't trigger anything, as call is ending
        res_context = CallContextEnum.GOODBYE
        text = await CONFIG.prompts.tts.goodbye(call)

    await handle_recognize_text(
        call=call,
        client=client,
        context=res_context,
        store=False,  # Do not store timeout prompt as it perturbs the LLM and makes it hallucinate
        text=text,
        timeout_error=timeout_error,
    )


@tracer.start_as_current_span("on_speech_unknown_error")
async def on_speech_unknown_error(
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
        store=False,  # Do not store error prompt as it perturbs the LLM and makes it hallucinate
        text=await CONFIG.prompts.tts.error(call),
    )


@tracer.start_as_current_span("on_play_completed")
async def on_play_completed(
    call: CallStateModel,
    client: CallAutomationClient,
    contexts: Optional[list[CallContextEnum]],
    post_callback: Callable[[CallStateModel], None],
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
    post_callback: Callable[[CallStateModel], None],
    trainings_callback: Callable[[CallStateModel], None],
) -> None:
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
            persist_coro,  # First, persist language change for next messages
            handle_recognize_text(
                call=call,
                client=client,
                text=await CONFIG.prompts.tts.hello(call),
            ),  # Second, greet the user
            load_llm_chat(
                call=call,
                client=client,
                post_callback=post_callback,
                trainings_callback=trainings_callback,
            ),  # Third, the LLM should be loaded to continue the conversation
        )  # All in parallel to lower the answer latency

    else:  # Returning call
        await asyncio.gather(
            persist_coro,  # First, persist language change for next messages
            handle_recognize_text(
                call=call,
                client=client,
                style=MessageStyleEnum.CHEERFUL,
                text=await CONFIG.prompts.tts.welcome_back(call),
                timeout_error=False,  # Do not trigger timeout, as the chat will continue
            ),  # Second, welcome back the user
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
        text=await CONFIG.prompts.tts.calltransfer_failure(call),
    )


@tracer.start_as_current_span("on_sms_received")
async def on_sms_received(
    call: CallStateModel,
    client: CallAutomationClient,
    message: str,
    post_callback: Callable[[CallStateModel], None],
    trainings_callback: Callable[[CallStateModel], None],
) -> bool:
    logger.info(f"SMS received from {call.initiate.phone_number}: {message}")
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
    post_callback: Callable[[CallStateModel], None],
) -> None:
    await handle_hangup(client=client, call=call)
    call.messages.append(
        MessageModel(
            content="",
            persona=MessagePersonaEnum.HUMAN,
            action=MessageActionEnum.HANGUP,
        )
    )
    post_callback(call)


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
    content = await llm_completion(
        text=CONFIG.prompts.llm.sms_summary_system(call),
        call=call,
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
        logger.warning("Error generating synthesis")
        return

    logger.info(f"Short synthesis: {short}")
    logger.info(f"Long synthesis: {long}")

    call.synthesis = SynthesisModel(
        long=long,
        short=short,
    )
    await _db.call_aset(call)


async def _intelligence_next(call: CallStateModel) -> None:
    """
    Generate next action for the call.
    """
    next = await llm_model(
        call=call,
        model=NextModel,
        text=CONFIG.prompts.llm.next_system(call),
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
