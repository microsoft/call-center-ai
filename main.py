# First imports, to make sure the following logs are first
from helpers.logging import build_logger
from helpers.config import CONFIG


_logger = build_logger(__name__)
_logger.info(f"claim-ai v{CONFIG.version}")


# General imports
from typing import Any, Callable, Coroutine, List, Optional, Tuple
from azure.communication.callautomation import (
    CallAutomationClient,
    CallConnectionClient,
    FileSource,
    PhoneNumberIdentifier,
    RecognizeInputType,
    SsmlSource,
)
from azure.communication.sms import SmsClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError
from azure.core.exceptions import ResourceNotFoundError
from azure.core.messaging import CloudEvent
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.identity import DefaultAzureCredential
from enum import Enum
from fastapi import FastAPI, status, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from helpers.config_models.database import Mode as DatabaseMode
from jinja2 import Environment, FileSystemLoader, select_autoescape
from models.action import ActionModel, Indent as IndentAction
from models.call import CallModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from persistence.ai_search import AiSearchSearch
from persistence.cosmos import CosmosStore
from persistence.sqlite import SqliteStore
from urllib.parse import quote_plus
import asyncio
import html
import re
from models.message import (
    Action as MessageAction,
    MessageModel,
    Persona as MessagePersona,
    ToolModel as MessageToolModel,
)
from models.claim import ClaimModel
from pydantic import ValidationError
from uuid import UUID
import json
import mistune


# Jinja configuration
jinja = Environment(
    autoescape=select_autoescape(),
    enable_async=True,
    loader=FileSystemLoader("public_website"),
)
# Jinja custom functions
jinja.filters["quote_plus"] = lambda x: quote_plus(str(x)) if x else ""
jinja.filters["markdown"] = lambda x: mistune.create_markdown(escape=False, plugins=["abbr", "speedup", "url"])(x) if x else "" # type: ignore

# Azure Communication Services
source_caller = PhoneNumberIdentifier(CONFIG.communication_service.phone_number)
_logger.info(f"Using phone number {str(CONFIG.communication_service.phone_number)}")
# Cannot place calls with RBAC, need to use access key (see: https://learn.microsoft.com/en-us/azure/communication-services/concepts/authentication#authentication-options)
call_automation_client = CallAutomationClient(
    endpoint=CONFIG.communication_service.endpoint,
    credential=AzureKeyCredential(
        CONFIG.communication_service.access_key.get_secret_value()
    ),
)
sms_client = SmsClient(
    credential=DefaultAzureCredential(), endpoint=CONFIG.communication_service.endpoint
)

# Persistence
db = (
    SqliteStore(CONFIG.database.sqlite)
    if CONFIG.database.mode == DatabaseMode.SQLITE
    else CosmosStore(CONFIG.database.cosmos_db)
)
search = AiSearchSearch(CONFIG.ai_search)

# FastAPI
_logger.info(f'Using root path "{CONFIG.api.root_path}"')
api = FastAPI(
    contact={
        "url": "https://github.com/clemlesne/claim-ai-phone-bot",
    },
    description="AI-powered call center solution with Azure and OpenAI GPT.",
    license_info={
        "name": "Apache-2.0",
        "url": "https://github.com/clemlesne/claim-ai-phone-bot/blob/master/LICENCE",
    },
    root_path=CONFIG.api.root_path,
    title="claim-ai-phone-bot",
    version=CONFIG.version,
)


CALL_EVENT_URL = f'{CONFIG.api.events_domain.strip("/")}/call/event'
CALL_INBOUND_URL = f'{CONFIG.api.events_domain.strip("/")}/call/inbound'
SENTENCE_R = r"[^\w\s+\-/'\",:;()]"
MESSAGE_ACTION_R = rf"(?:{'|'.join([action.value for action in MessageAction])}):"
FUNC_NAME_SANITIZER_R = r"[^a-zA-Z0-9_-]"


class Context(str, Enum):
    TRANSFER_FAILED = "transfer_failed"
    CONNECT_AGENT = "connect_agent"
    GOODBYE = "goodbye"


@api.get(
    "/health/liveness",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Liveness healthckeck, always returns 204, used to check if the API is up.",
)
async def health_liveness_get() -> None:
    pass


@api.get(
    "/report/{phone_number}",
    description="Display the history of calls in a web page.",
)
async def report_history_get(phone_number: str) -> HTMLResponse:
    calls = await db.call_asearch_all(phone_number) or []

    template = jinja.get_template("history.html.jinja")
    render = await template.render_async(
        bot_company=CONFIG.workflow.bot_company,
        bot_name=CONFIG.workflow.bot_name,
        calls=calls,
        phone_number=phone_number,
        version=CONFIG.version,
    )
    return HTMLResponse(content=render)


@api.get(
    "/report/{phone_number}/{call_id}",
    description="Display the call report in a web page.",
)
async def report_call_get(phone_number: str, call_id: UUID) -> HTMLResponse:
    call = await db.call_aget(call_id)
    if not call or call.phone_number != phone_number:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call {call_id} for phone number {phone_number} not found",
        )

    template = jinja.get_template("report.html.jinja")
    render = await template.render_async(
        bot_company=CONFIG.workflow.bot_company,
        bot_name=CONFIG.workflow.bot_name,
        call=call,
        version=CONFIG.version,
    )
    return HTMLResponse(content=render)


@api.get(
    "/call",
    description="Get all calls by phone number.",
)
async def call_get(phone_number: str) -> List[CallModel]:
    return await db.call_asearch_all(phone_number) or []


@api.get(
    "/call/initiate",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Initiate an outbound call to a phone number.",
)
async def call_initiate_get(phone_number: str) -> None:
    _logger.info(f"Initiating outbound call to {phone_number}")
    call_connection_properties = call_automation_client.create_call(
        callback_url=await callback_url(phone_number),
        cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
        source_caller_id_number=source_caller,
        target_participant=PhoneNumberIdentifier(phone_number),
    )
    _logger.info(
        f"Created call with connection id: {call_connection_properties.call_connection_id}"
    )


@api.post(
    "/call/inbound",
    description="Handle incoming call from a Azure Event Grid event originating from Azure Communication Services.",
)
async def call_inbound_post(request: Request):
    for event_dict in await request.json():
        event = EventGridEvent.from_dict(event_dict)
        event_type = event.event_type

        _logger.debug(f"Call inbound event {event_type} with data {event.data}")

        if event_type == SystemEventNames.EventGridSubscriptionValidationEventName:
            validation_code = event.data["validationCode"]
            _logger.info(f"Validating Event Grid subscription ({validation_code})")
            return JSONResponse(
                content={"validationResponse": event.data["validationCode"]},
                status_code=status.HTTP_200_OK,
            )

        elif event_type == SystemEventNames.AcsIncomingCallEventName:
            if event.data["from"]["kind"] == "phoneNumber":
                phone_number = event.data["from"]["phoneNumber"]["value"]
            else:
                phone_number = event.data["from"]["rawId"]

            _logger.debug(f"Incoming call handler caller ID: {phone_number}")
            call_context = event.data["incomingCallContext"]
            answer_call_result = call_automation_client.answer_call(
                callback_url=await callback_url(phone_number),
                cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
                incoming_call_context=call_context,
            )
            _logger.info(
                f"Answered call with {phone_number} ({answer_call_result.call_connection_id})"
            )


@api.post(
    "/call/event/{phone_number}",
    description="Handle callbacks from Azure Communication Services.",
    status_code=status.HTTP_204_NO_CONTENT,
)
# TODO: Secure this endpoint with a secret
# See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/secure-webhook-endpoint.md
async def call_event_post(
    request: Request, background_tasks: BackgroundTasks, phone_number: str
) -> None:
    for event_dict in await request.json():
        background_tasks.add_task(
            communication_event_worker, background_tasks, event_dict, phone_number
        )


async def communication_event_worker(
    background_tasks: BackgroundTasks, event_dict: dict, phone_number: str
) -> None:
    call = await db.call_asearch_one(phone_number)
    if not call:
        _logger.warn(f"Call with {phone_number} not found")
        return

    event = CloudEvent.from_dict(event_dict)
    connection_id = event.data["callConnectionId"]
    operation_context = event.data.get("operationContext", None)
    client = call_automation_client.get_call_connection(
        call_connection_id=connection_id
    )
    event_type = event.type

    _logger.debug(f"Call event received {event_type} for call {call}")
    _logger.debug(event.data)

    if event_type == "Microsoft.Communication.CallConnected":  # Call answered
        _logger.info(f"Call connected ({call.call_id})")
        call.recognition_retry = 0  # Reset recognition retry counter

        call.messages.append(
            MessageModel(
                action=MessageAction.CALL,
                content="",
                persona=MessagePersona.HUMAN,
            )
        )

        if len(call.messages) == 1:  # First call
            await handle_recognize_text(
                call=call, client=client, text=CONFIG.prompts.tts.hello()
            )

        else:  # Returning call
            await handle_play(
                call=call,
                client=client,
                text=CONFIG.prompts.tts.welcome_back(),
            )
            call = await intelligence(background_tasks, call, client)

    elif event_type == "Microsoft.Communication.CallDisconnected":  # Call hung up
        _logger.info(f"Call disconnected ({call.call_id})")
        await handle_hangup(call=call, client=client)

    elif (
        event_type == "Microsoft.Communication.RecognizeCompleted"
    ):  # Speech recognized
        if event.data["recognitionType"] == "speech":
            speech_text = event.data["speechResult"]["speech"]
            _logger.info(f"Recognition completed ({call.call_id}): {speech_text}")

            if speech_text is not None and len(speech_text) > 0:
                call.messages.append(
                    MessageModel(content=speech_text, persona=MessagePersona.HUMAN)
                )
                call = await intelligence(background_tasks, call, client)

    elif (
        event_type == "Microsoft.Communication.RecognizeFailed"
    ):  # Speech recognition failed
        result_information = event.data["resultInformation"]
        error_code = result_information["subCode"]

        # Error codes:
        # 8510 = Action failed, initial silence timeout reached
        # 8532 = Action failed, inter-digit silence timeout reached
        # 8512 = Unknown internal server error
        # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/recognize-action.md#event-codes
        if (
            error_code in (8510, 8532, 8512) and call.recognition_retry < 10
        ):  # Timeout retry
            await handle_recognize_text(
                call=call,
                client=client,
                text=CONFIG.prompts.tts.timeout_silence(),
            )
            call.recognition_retry += 1

        else:  # Timeout reached or other error
            await handle_play(
                call=call,
                client=client,
                context=Context.GOODBYE,
                text=CONFIG.prompts.tts.goodbye(),
            )

    elif event_type == "Microsoft.Communication.PlayCompleted":  # Media played
        _logger.debug(f"Play completed ({call.call_id})")

        if (
            operation_context == Context.TRANSFER_FAILED
            or operation_context == Context.GOODBYE
        ):  # Call ended
            _logger.info(f"Ending call ({call.call_id})")
            await handle_hangup(call=call, client=client)

        elif operation_context == Context.CONNECT_AGENT:  # Call transfer
            _logger.info(f"Initiating transfer call initiated ({call.call_id})")
            agent_caller = PhoneNumberIdentifier(
                str(CONFIG.workflow.agent_phone_number)
            )
            client.transfer_call_to_participant(target_participant=agent_caller)

    elif event_type == "Microsoft.Communication.PlayFailed":  # Media play failed
        _logger.debug(f"Play failed ({call.call_id})")

        result_information = event.data["resultInformation"]
        error_code = result_information["subCode"]

        # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/play-action.md
        if error_code == 8535:  # Action failed, file format is invalid
            _logger.warn("Error during media play, file format is invalid")
        elif error_code == 8536:  # Action failed, file could not be downloaded
            _logger.warn("Error during media play, file could not be downloaded")
        elif error_code == 9999:  # Unknown internal server error
            _logger.warn("Error during media play, unknown internal server error")
        else:
            _logger.warn(f"Error during media play, unknown error code {error_code}")

    elif (
        event_type == "Microsoft.Communication.CallTransferAccepted"
    ):  # Call transfer accepted
        _logger.info(f"Call transfer accepted event ({call.call_id})")
        # TODO: Is there anything to do here?

    elif (
        event_type == "Microsoft.Communication.CallTransferFailed"
    ):  # Call transfer failed
        _logger.debug(f"Call transfer failed event ({call.call_id})")
        result_information = event.data["resultInformation"]
        sub_code = result_information["subCode"]
        _logger.info(f"Error during call transfer, subCode {sub_code} ({call.call_id})")
        await handle_play(
            call=call,
            client=client,
            context=Context.TRANSFER_FAILED,
            text=CONFIG.prompts.tts.calltransfer_failure(),
        )

    await db.call_aset(call)


async def intelligence(
    background_tasks: BackgroundTasks, call: CallModel, client: CallConnectionClient
) -> CallModel:
    """
    Handle the intelligence of the call, including: GPT chat, GPT completion, TTS, and media play.

    Play the loading sound while waiting for the intelligence to be processed. If the intelligence is not processed after 15 seconds, play the timeout sound. If the intelligence is not processed after 30 seconds, stop the intelligence processing and play the error sound.
    """
    has_started = False

    async def gpt_callback(text: str) -> None:
        nonlocal has_started

        if not await safety_check(text):
            _logger.warn(f"Unsafe text detected, not playing ({call.call_id})")
            return

        has_started = True
        await handle_play(
            call=call,
            client=client,
            store=False,
            text=text,
        )

    chat_task = asyncio.create_task(gpt_chat(background_tasks, call, gpt_callback))
    soft_timeout_task = asyncio.create_task(
        asyncio.sleep(CONFIG.workflow.intelligence_soft_timeout_sec)
    )
    soft_timeout_triggered = False
    hard_timeout_task = asyncio.create_task(
        asyncio.sleep(CONFIG.workflow.intelligence_hard_timeout_sec)
    )
    chat_action = None

    try:
        while True:
            _logger.debug(f"Chat task status ({call.call_id}): {chat_task.done()}")
            if chat_task.done():  # Break when chat coroutine is done
                # Clean up
                soft_timeout_task.cancel()
                hard_timeout_task.cancel()
                # Answer with chat result
                call, chat_action = chat_task.result()
                break
            if hard_timeout_task.done():  # Break when hard timeout is reached
                _logger.warn(
                    f"Hard timeout of {CONFIG.workflow.intelligence_hard_timeout_sec}s reached ({call.call_id})"
                )
                # Clean up
                chat_task.cancel()
                soft_timeout_task.cancel()
                break
            if not has_started:  # Catch timeout if async loading is not started
                if (
                    soft_timeout_task.done() and not soft_timeout_triggered
                ):  # Speak when soft timeout is reached
                    _logger.warn(
                        f"Soft timeout of {CONFIG.workflow.intelligence_soft_timeout_sec}s reached ({call.call_id})"
                    )
                    soft_timeout_triggered = True
                    await handle_play(
                        call=call,
                        client=client,
                        text=CONFIG.prompts.tts.timeout_loading(),
                    )
                else:  # Do not play timeout prompt plus loading, it can be frustrating for the user
                    await handle_media(
                        call=call,
                        client=client,
                        sound_url=CONFIG.prompts.sounds.loading(),
                    )  # Play loading sound
            # Wait to not block the event loop and play too many sounds
            await asyncio.sleep(5)
    except Exception:
        _logger.warn(f"Error loading intelligence ({call.call_id})", exc_info=True)

    # For any error reason, answer with error
    if not chat_action:
        _logger.debug(
            f"Error loading intelligence ({call.call_id}), answering with default error"
        )
        chat_action = ActionModel(
            content=CONFIG.prompts.tts.error(), intent=IndentAction.CONTINUE
        )

    _logger.debug(f"Chat ({call.call_id}): {chat_action}")

    if chat_action.intent == IndentAction.TALK_TO_HUMAN:
        await handle_play(
            call=call,
            client=client,
            context=Context.CONNECT_AGENT,
            text=CONFIG.prompts.tts.end_call_to_connect_agent(),
        )

    elif chat_action.intent == IndentAction.END_CALL:
        await handle_play(
            call=call,
            client=client,
            context=Context.GOODBYE,
            text=CONFIG.prompts.tts.goodbye(),
        )

    elif chat_action.intent in (
        IndentAction.NEW_CLAIM,
        IndentAction.UPDATED_CLAIM,
        IndentAction.NEW_OR_UPDATED_REMINDER,
    ):
        # Save in DB for new claims and allowing demos to be more "real-time"
        await db.call_aset(call)
        # Recursively call intelligence to continue the conversation
        call = await intelligence(background_tasks, call, client)

    else:
        await handle_recognize_text(
            call=call,
            client=client,
        )

    return call


async def handle_play(
    client: CallConnectionClient,
    call: CallModel,
    text: str,
    context: Optional[str] = None,
    store: bool = True,
) -> None:
    """
    Play a text to a call participant.

    If store is True, the text will be stored in the call messages. Compatible with text larger than 400 characters, in that case the text will be split in chunks and played sequentially.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
    """
    if store:
        call.messages.append(
            MessageModel(content=text, persona=MessagePersona.ASSISTANT)
        )

    _logger.info(f"Playing text ({call.call_id}): {text}")

    # Split text in chunks of max 400 characters, separated by a comma
    chunks = []
    chunk = ""
    for word in text.split("."):  # Split by sentence
        to_add = f"{word}. "
        if len(chunk) + len(to_add) >= 400:
            chunks.append(chunk)
            chunk = ""
        chunk += to_add
    if chunk:
        chunks.append(chunk)

    try:
        for chunk in chunks:
            _logger.debug(f"Playing chunk ({call.call_id}): {chunk}")
            client.play_media(
                operation_context=context,
                play_source=audio_from_text(chunk),
            )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing ({call.call_id})")


async def gpt_completion(system: str, call: CallModel, max_tokens: int) -> str:
    _logger.debug(f"Running GPT completion ({call.call_id})")

    messages: List[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.default_system(
                phone_number=call.phone_number,
            ),
            role="system",
        ),
        ChatCompletionSystemMessageParam(
            content=system,
            role="system",
        ),
    ]
    _logger.debug(f"Messages: {messages}")

    content = None
    try:
        res = await completion_sync(
            max_tokens=max_tokens,
            messages=messages,
        )
        content = res.content

    except Exception:
        _logger.warn(f"OpenAI API call error", exc_info=True)

    return content or ""


async def gpt_chat(
    background_tasks: BackgroundTasks,
    call: CallModel,
    user_callback: Callable[[str], Coroutine[Any, Any, None]],
    retry_attempt: int = 0,
) -> Tuple[CallModel, ActionModel]:
    _logger.debug(f"Running GPT chat ({call.call_id})")

    def _error_response() -> Tuple[CallModel, ActionModel]:
        return (
            call,
            ActionModel(
                content=CONFIG.prompts.tts.error(),
                intent=IndentAction.CONTINUE,
            ),
        )

    # Query expansion from last messages
    trainings_tasks = await asyncio.gather(
        *[
            search.training_asearch_all(message.content)
            for message in call.messages[-5:]
        ],
    )
    trainings = sorted(
        set(training for trainings in trainings_tasks for training in trainings or [])
    )  # Flatten, remove duplicates, and sort by score
    _logger.info(f"Enhancing GPT chat with {len(trainings)} trainings ({call.call_id})")
    _logger.debug(f"Trainings: {trainings}")

    messages: List[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.default_system(
                phone_number=call.phone_number,
            ),
            role="system",
        ),
        ChatCompletionSystemMessageParam(
            content=CONFIG.prompts.llm.chat_system(
                claim=call.claim,
                reminders=call.reminders,
                trainings=trainings,
            ),
            role="system",
        ),
    ]
    for message in call.messages:
        if message.persona == MessagePersona.HUMAN:
            messages.append(
                ChatCompletionUserMessageParam(
                    content=f"{message.action.value}: {message.content}",
                    role="user",
                )
            )
        elif message.persona == MessagePersona.ASSISTANT:
            if not message.tool_calls:
                messages.append(
                    ChatCompletionAssistantMessageParam(
                        content=f"{message.action.value}: {message.content}",
                        role="assistant",
                    )
                )
            else:
                messages.append(
                    ChatCompletionAssistantMessageParam(
                        content=f"{message.action.value}: {message.content}",
                        role="assistant",
                        tool_calls=[
                            ChatCompletionMessageToolCallParam(
                                id=tool_call.tool_id,
                                type="function",
                                function={
                                    "arguments": tool_call.function_arguments,
                                    "name": "-".join(
                                        re.sub(
                                            FUNC_NAME_SANITIZER_R,
                                            "-",
                                            tool_call.function_name,
                                        ).split("-")
                                    ),  # Sanitize with dashes then deduplicate dashes, backward compatibility with old models
                                },
                            )
                            for tool_call in message.tool_calls
                        ],
                    )
                )
                for tool_call in message.tool_calls:
                    messages.append(
                        ChatCompletionToolMessageParam(
                            content=tool_call.content,
                            role="tool",
                            tool_call_id=tool_call.tool_id,
                        )
                    )
    _logger.debug(f"Messages: {messages}")

    customer_response_prop = "customer_response"
    tools: List[ChatCompletionToolParam] = [
        ChatCompletionToolParam(
            type="function",
            function={
                "description": "Use this if the user wants to talk to a human and Assistant is unable to help. This will transfer the customer to an human agent. Approval from the customer must be explicitely given. Example: 'I want to talk to a human', 'I want to talk to a real person'.",
                "name": IndentAction.TALK_TO_HUMAN.value,
                "parameters": {
                    "properties": {},
                    "required": [],
                    "type": "object",
                },
            },
        ),
        ChatCompletionToolParam(
            type="function",
            function={
                "description": "Use this if the user wants to end the call, or if the user said goodbye in the current call. Be warnging that the call will be ended immediately. Example: 'I want to hang up', 'Good bye, see you soon', 'We are done here', 'We will talk again later'.",
                "name": IndentAction.END_CALL.value,
                "parameters": {
                    "properties": {},
                    "required": [],
                    "type": "object",
                },
            },
        ),
        ChatCompletionToolParam(
            type="function",
            function={
                "description": "Use this if the user wants to create a new claim for a totally different subject. This will reset the claim and reminder data. Old is stored but not accessible anymore. Approval from the customer must be explicitely given. Example: 'I want to create a new claim'.",
                "name": IndentAction.NEW_CLAIM.value,
                "parameters": {
                    "properties": {
                        f"{customer_response_prop}": {
                            "description": "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre', 'I am updating the contact contact info to 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
                            "type": "string",
                        }
                    },
                    "required": [
                        customer_response_prop,
                    ],
                    "type": "object",
                },
            },
        ),
        ChatCompletionToolParam(
            type="function",
            function={
                "description": "Use this if the user wants to update a claim field with a new value. Example: 'Update claim explanation to: I was driving on the highway when a car hit me from behind', 'Update contact contact info to: 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
                "name": IndentAction.UPDATED_CLAIM.value,
                "parameters": {
                    "properties": {
                        "field": {
                            "description": "The claim field to update.",
                            "enum": list(ClaimModel.editable_fields()),
                            "type": "string",
                        },
                        "value": {
                            "description": "The claim field value to update.",
                            "type": "string",
                        },
                        f"{customer_response_prop}": {
                            "description": "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre', 'I am updating the contact contact info to 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
                            "type": "string",
                        },
                    },
                    "required": [
                        customer_response_prop,
                        "field",
                        "value",
                    ],
                    "type": "object",
                },
            },
        ),
        ChatCompletionToolParam(
            type="function",
            function={
                "description": "Use this if you think there is something important to do in the future, and you want to be reminded about it. If it already exists, it will be updated with the new values. Example: 'Remind Assitant thuesday at 10am to call back the customer', 'Remind Assitant next week to send the report', 'Remind the customer next week to send the documents by the end of the month'.",
                "name": IndentAction.NEW_OR_UPDATED_REMINDER.value,
                "parameters": {
                    "properties": {
                        "description": {
                            "description": "Contextual description of the reminder. Should be detailed enough to be understood by anyone. Example: 'Watch model is Rolex Submariner 116610LN', 'User said the witnesses car was red but the police report says it was blue. Double check with the involved parties'.",
                            "type": "string",
                        },
                        "due_date_time": {
                            "description": "Datetime when the reminder should be triggered. Should be in the future, in the ISO format.",
                            "type": "string",
                        },
                        "title": {
                            "description": "Short title of the reminder. Should be short and concise, in the format 'Verb + Subject'. Title is unique and allows the reminder to be updated. Example: 'Call back customer', 'Send analysis report', 'Study replacement estimates for the stolen watch'.",
                            "type": "string",
                        },
                        "owner": {
                            "description": "The owner of the reminder. Can be 'customer', 'assistant', or a third party from the claim. Try to be as specific as possible, with a name. Example: 'customer', 'assistant', 'contact', 'witness', 'police'.",
                            "type": "string",
                        },
                        f"{customer_response_prop}": {
                            "description": "The text to be read to the customer to confirm the reminder. Only speak about this action. Use an imperative sentence. Example: 'I am creating a reminder for next week to call back the customer', 'I am creating a reminder for next week to send the report'.",
                            "type": "string",
                        },
                    },
                    "required": [
                        customer_response_prop,
                        "description",
                        "due_date_time",
                        "title",
                        "owner",
                    ],
                    "type": "object",
                },
            },
        ),
    ]
    _logger.debug(f"Tools: {tools}")

    full_content = ""
    buffer_content = ""
    tool_calls = {}
    try:
        async for delta in completion_stream(
            max_tokens=400,
            messages=messages,
            tools=tools,
        ):
            if delta.content is None:
                for piece in delta.tool_calls or []:
                    tool_calls[piece.index] = tool_calls.get(
                        piece.index,
                        {
                            "function": {"arguments": "", "name": ""},
                            "id": None,
                            "type": "function",
                        },
                    )
                    if piece.id:
                        tool_calls[piece.index]["id"] = piece.id
                    if piece.function:
                        if piece.function.name:
                            tool_calls[piece.index]["function"][
                                "name"
                            ] = piece.function.name
                        tool_calls[piece.index]["function"][
                            "arguments"
                        ] += piece.function.arguments
            else:
                # Store whole content
                full_content += delta.content
                buffer_content += delta.content
                # Remove tool calls from buffer content, if any
                buffer_content = _remove_message_actions(buffer_content)
                # Test if there ia a sentence in the buffer
                separators = re.findall(SENTENCE_R, buffer_content)
                if separators and separators[0] in buffer_content:
                    to_return = re.split(SENTENCE_R, buffer_content)[0] + separators[0]
                    buffer_content = buffer_content[len(to_return) :]
                    await user_callback(to_return.strip())

        if buffer_content:
            # Batch remaining user return
            await user_callback(buffer_content)

        # Remove tool calls from full content, if any
        full_content = _remove_message_actions(full_content)

        _logger.debug(f"Chat response: {full_content}")
        _logger.debug(f"Tool calls: {tool_calls}")

        # OpenAI GPT-4 Turbo sometimes return wrong tools schema, in that case, retry within limits
        # TODO: Tries to detect this error earlier
        # See: https://community.openai.com/t/model-tries-to-call-unknown-function-multi-tool-use-parallel/490653
        if any(
            tool_call["function"]["name"] == "multi_tool_use.parallel"
            for _, tool_call in tool_calls.items()
        ):
            if retry_attempt > 3:
                _logger.warn(
                    f'LLM send back invalid tool schema "multi_tool_use.parallel", retry limit reached'
                )
                return _error_response()
            _logger.warn(
                f'LLM send back invalid tool schema "multi_tool_use.parallel", retrying'
            )
            return await gpt_chat(
                background_tasks, call, user_callback, retry_attempt + 1
            )

        intent = IndentAction.CONTINUE
        models = []
        if tool_calls:
            # TODO: Catch tool error individually
            for _, tool_call in tool_calls.items():
                name = tool_call["function"]["name"]
                arguments = tool_call["function"]["arguments"]
                _logger.info(f"Tool call {name} with parameters {arguments}")

                model = MessageToolModel(
                    content="",
                    function_arguments=arguments,
                    function_name=name,
                    tool_id=tool_call["id"],
                )

                if name == IndentAction.TALK_TO_HUMAN.value:
                    intent = IndentAction.TALK_TO_HUMAN

                elif name == IndentAction.END_CALL.value:
                    intent = IndentAction.END_CALL

                elif name == IndentAction.UPDATED_CLAIM.value:
                    intent = IndentAction.UPDATED_CLAIM
                    try:
                        parameters = json.loads(arguments)
                    except Exception:
                        _logger.warn(
                            f'LLM send back invalid JSON for "{arguments}", ignoring this tool call.'
                        )
                        continue

                    if not customer_response_prop in parameters:
                        _logger.warn(
                            f"Missing {customer_response_prop} prop in {arguments}, please fix this!"
                        )
                    else:
                        local_content = parameters[customer_response_prop]
                        full_content += local_content + " "
                        await user_callback(local_content)

                    content = None
                    if not parameters["field"] in ClaimModel.editable_fields():
                        content = f'Failed to update a non-editable field "{parameters['field']}".'
                    else:
                        try:
                            # Define the field
                            setattr(
                                call.claim, parameters["field"], parameters["value"]
                            )
                            # Trigger a re-validation to spot errors before saving
                            ClaimModel.model_validate(call.claim)
                        except ValidationError as e:
                            content = f'Failed to edit field "{parameters["field"]}": {e.json()}'
                    if not content:
                        content = f'Updated claim field "{parameters['field']}" with value "{parameters['value']}".'
                    model.content = content

                elif name == IndentAction.NEW_CLAIM.value:
                    intent = IndentAction.NEW_CLAIM
                    try:
                        parameters = json.loads(arguments)
                    except Exception:
                        _logger.warn(
                            f'LLM send back invalid JSON for "{arguments}", ignoring this tool call.'
                        )
                        continue

                    if not customer_response_prop in parameters:
                        _logger.warn(
                            f"Missing {customer_response_prop} prop in {arguments}, please fix this!"
                        )
                    else:
                        local_content = parameters[customer_response_prop]
                        full_content += local_content + " "
                        await user_callback(local_content)

                    # Generate synthesis for the old claim
                    background_tasks.add_task(post_call_synthesis, call)

                    # Add context of the last message, if not, LLM messed up and loop on this action
                    last_message = call.messages[-1]
                    call = CallModel(phone_number=call.phone_number)
                    call.messages.append(last_message)
                    model.content = "Claim, reminders and messages reset."

                elif name == IndentAction.NEW_OR_UPDATED_REMINDER.value:
                    intent = IndentAction.NEW_OR_UPDATED_REMINDER
                    try:
                        parameters = json.loads(arguments)
                    except Exception:
                        _logger.warn(
                            f'LLM send back invalid JSON for "{arguments}", ignoring this tool call.'
                        )
                        continue

                    if not customer_response_prop in parameters:
                        _logger.warn(
                            f'Missing "{customer_response_prop}" prop in "{arguments}", please fix this!'
                        )
                    else:
                        local_content = parameters[customer_response_prop]
                        full_content += local_content + " "
                        await user_callback(local_content)

                    updated = False
                    for reminder in call.reminders:
                        if reminder.title == parameters["title"]:
                            reminder.description = parameters["description"]
                            reminder.due_date_time = parameters["due_date_time"]
                            reminder.owner = parameters["owner"]
                            model.content = (
                                f'Reminder "{parameters['title']}" updated.'
                            )
                            updated = True
                            break

                    if not updated:
                        call.reminders.append(
                            ReminderModel(
                                description=parameters["description"],
                                due_date_time=parameters["due_date_time"],
                                title=parameters["title"],
                                owner=parameters["owner"],
                            )
                        )
                        model.content = f'Reminder "{parameters['title']}" created.'

                models.append(model)

        call.messages.append(
            MessageModel(
                content=full_content,
                persona=MessagePersona.ASSISTANT,
                tool_calls=models,
            )
        )

        return (
            call,
            ActionModel(
                content=full_content,
                intent=intent,
            ),
        )

    except Exception:
        _logger.warn(f"OpenAI API call error", exc_info=True)

    return _error_response()


async def handle_recognize_text(
    client: CallConnectionClient,
    call: CallModel,
    text: Optional[str] = None,
    store: bool = True,
) -> None:
    """
    Play a text to a call participant and start recognizing the response.

    If store is True, the text will be stored in the call messages. Starts by playing text, then the "ready" sound, and finally starts recognizing the response.
    """
    if text:
        await handle_play(
            call=call,
            client=client,
            store=store,
            text=text,
        )

    _logger.debug(f"Recognizing ({call.call_id})")
    await handle_recognize_media(
        call=call,
        client=client,
        sound_url=CONFIG.prompts.sounds.ready(),
    )


async def handle_recognize_media(
    client: CallConnectionClient,
    call: CallModel,
    sound_url: str,
) -> None:
    """
    Play a media to a call participant and start recognizing the response.

    TODO: Disable or lower profanity filter. The filter seems enabled by default, it replaces words like "holes in my roof" by "*** in my roof". This is not acceptable for a call center.
    """
    try:
        client.start_recognizing_media(
            end_silence_timeout=3,  # Sometimes user includes breaks in their speech
            input_type=RecognizeInputType.SPEECH,
            play_prompt=FileSource(url=sound_url),
            speech_language=CONFIG.workflow.conversation_lang,
            target_participant=PhoneNumberIdentifier(call.phone_number),
        )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before recognizing ({call.call_id})")


async def handle_media(
    client: CallConnectionClient,
    call: CallModel,
    sound_url: str,
    context: Optional[str] = None,
) -> None:
    try:
        client.play_media(
            operation_context=context,
            play_source=FileSource(url=sound_url),
        )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing ({call.call_id})")


async def handle_hangup(client: CallConnectionClient, call: CallModel) -> None:
    _logger.debug(f"Hanging up call ({call.call_id})")
    try:
        client.hang_up(is_for_everyone=True)
    except ResourceNotFoundError:
        _logger.debug(f"Call already hung up ({call.call_id})")

    call.messages.append(
        MessageModel(
            content="",
            persona=MessagePersona.HUMAN,
            action=MessageAction.HANGUP,
        )
    )

    # Start post-call intelligence
    await asyncio.gather(
        post_call_sms(call),
        post_call_synthesis(call),
    )


async def post_call_sms(call: CallModel) -> None:
    """
    Send an SMS report to the customer.
    """
    content = await gpt_completion(
        system=CONFIG.prompts.llm.sms_summary_system(
            call.claim, call.messages, call.reminders
        ),
        call=call,
        max_tokens=300,
    )
    _logger.info(f"SMS report ({call.call_id}): {content}")

    try:
        responses = sms_client.send(
            from_=str(CONFIG.communication_service.phone_number),
            message=content,
            to=call.phone_number,
        )
        response = responses[0]

        if response.successful:
            _logger.debug(
                f"SMS report sent {response.message_id} to {response.to} ({call.call_id})"
            )
            call.messages.append(
                MessageModel(
                    action=MessageAction.SMS,
                    content=content,
                    persona=MessagePersona.ASSISTANT,
                )
            )
        else:
            _logger.warn(
                f"Failed SMS to {response.to}, status {response.http_status_code}, error {response.error_message} ({call.call_id})"
            )

    except ClientAuthenticationError:
        _logger.error("Authentication error for SMS, check the credentials")
    except Exception:
        _logger.warn(
            f"Failed SMS to {call.phone_number} ({call.call_id})", exc_info=True
        )


def audio_from_text(text: str) -> SsmlSource:
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
    ssml = f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{CONFIG.workflow.conversation_lang}"><voice name="{CONFIG.communication_service.voice_name}" effect="eq_telecomhp8k"><lexicon uri="{CONFIG.resources.public_url}/lexicon.xml"/><prosody rate="0.95">{text}</prosody></voice></speak>'
    return SsmlSource(ssml_text=ssml)


async def callback_url(caller_id: str) -> str:
    """
    Generate the callback URL for a call.

    If the caller has already called, use the same call ID, to keep the conversation history. Otherwise, create a new call ID.
    """
    call = await db.call_asearch_one(caller_id)
    if not call:
        call = CallModel(phone_number=caller_id)
        await db.call_aset(call)
    return f"{CALL_EVENT_URL}/{html.escape(call.phone_number)}"


async def post_call_synthesis(call: CallModel) -> None:
    """
    Synthesize the call and store it to the model.
    """
    _logger.debug(f"Synthesizing call ({call.call_id})")

    short, long = await asyncio.gather(
        gpt_completion(
            system=CONFIG.prompts.llm.synthesis_short_system(
                call.claim, call.messages, call.reminders
            ),
            call=call,
            max_tokens=100,
        ),
        gpt_completion(
            system=CONFIG.prompts.llm.citations(
                claim=call.claim,
                messages=call.messages,
                reminders=call.reminders,
                text=await gpt_completion(
                    system=CONFIG.prompts.llm.synthesis_long_system(
                        claim=call.claim,
                        messages=call.messages,
                        reminders=call.reminders,
                    ),
                    call=call,
                    max_tokens=1000,
                ),
            ),
            call=call,
            max_tokens=1000,
        ),
    )
    _logger.info(f"Short synthesis ({call.call_id}): {short}")
    _logger.info(f"Long synthesis ({call.call_id}): {long}")

    call.synthesis = SynthesisModel(
        long=long,
        short=short,
    )
    await db.call_aset(call)


def _remove_message_actions(text: str) -> str:
    """
    Remove action from content. AI often adds it by mistake event if explicitly asked not to.
    """
    return re.sub(MESSAGE_ACTION_R, "", text).strip()
