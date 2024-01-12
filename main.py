from typing import Optional
from azure.communication.callautomation import (
    CallAutomationClient,
    CallConnectionClient,
    FileSource,
    PhoneNumberIdentifier,
    RecognizeInputType,
    TextSource,
)
from azure.communication.sms import SmsClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.core.messaging import CloudEvent
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.mgmt.core.polling.arm_polling import ARMPolling
from azure.mgmt.eventgrid import EventGridManagementClient
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from fastapi import FastAPI, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from helpers.config import CONFIG
from helpers.logging import build_logger
from helpers.prompts import LLM as LLMPrompt, TTS as TTSPrompt
from helpers.version import VERSION
from models.action import ActionModel, Indent as IndentAction
from models.reminder import ReminderModel
from pydantic.json import pydantic_encoder
from models.call import (
    CallModel,
    MessageModel as CallMessageModel,
    Persona as CallPersona,
    ToolModel as CallToolModel,
)
from models.claim import ClaimModel
from openai import AsyncAzureOpenAI
from os import environ
from uuid import UUID, uuid4
import asyncio
import json
import sqlite3


_logger = build_logger(__name__)

ROOT_PATH = CONFIG.api.root_path
AZ_CREDENTIAL = DefaultAzureCredential()

_logger.info(f'Using root path "{ROOT_PATH}"')

oai_gpt = AsyncAzureOpenAI(
    api_version="2023-12-01-preview",
    azure_ad_token_provider=get_bearer_token_provider(
        AZ_CREDENTIAL, "https://cognitiveservices.azure.com/.default"
    ),
    azure_endpoint=CONFIG.openai.endpoint,
    azure_deployment=CONFIG.openai.gpt_deployment,
)
eventgrid_subscription_name = f"tmp-{uuid4()}"
eventgrid_mgmt_client = EventGridManagementClient(
    credential=DefaultAzureCredential(),
    subscription_id=CONFIG.eventgrid.subscription_id,
)
source_caller = PhoneNumberIdentifier(CONFIG.communication_service.phone_number)
# Cannot place calls with RBAC, need to use access key (see: https://learn.microsoft.com/en-us/azure/communication-services/concepts/authentication#authentication-options)
call_automation_client = CallAutomationClient(
    endpoint=CONFIG.communication_service.endpoint,
    credential=AzureKeyCredential(
        CONFIG.communication_service.access_key.get_secret_value()
    ),
)
sms_client = SmsClient(credential=AZ_CREDENTIAL, endpoint=CONFIG.communication_service.endpoint)
db = sqlite3.connect(".local.sqlite", check_same_thread=False)

EVENTS_DOMAIN = environ.get("EVENTS_DOMAIN").strip("/")
assert EVENTS_DOMAIN, "EVENTS_DOMAIN environment variable is not set"
CALL_EVENT_URL = f"{EVENTS_DOMAIN}/call/event"
CALL_INBOUND_URL = f"{EVENTS_DOMAIN}/call/inbound"


class Context(str, Enum):
    TRANSFER_FAILED = "transfer_failed"
    CONNECT_AGENT = "connect_agent"
    GOODBYE = "goodbye"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task = asyncio.create_task(eventgrid_register())  # Background task
    yield
    task.cancel()
    eventgrid_unregister()  # Foreground task


api = FastAPI(
    contact={
        "url": "https://github.com/clemlesne/claim-ai-phone-bot",
    },
    description="AI-powered call center solution with Azure and OpenAI GPT.",
    license_info={
        "name": "Apache-2.0",
        "url": "https://github.com/clemlesne/claim-ai-phone-bot/blob/master/LICENCE",
    },
    lifespan=lifespan,
    root_path=ROOT_PATH,
    title="claim-ai-phone-bot",
    version=VERSION,
)

api.add_middleware(
    CORSMiddleware,
    allow_headers=["*"],
    allow_methods=["*"],
    allow_origins=["*"],
)


async def eventgrid_register() -> None:
    def callback(future: ARMPolling):
        _logger.info(f"Event Grid subscription created (status {future.status()})")

    _logger.info(f"Creating Event Grid subscription {eventgrid_subscription_name}")
    eventgrid_mgmt_client.system_topic_event_subscriptions.begin_create_or_update(
        resource_group_name=CONFIG.eventgrid.resource_group,
        system_topic_name=CONFIG.eventgrid.system_topic,
        event_subscription_name=eventgrid_subscription_name,
        event_subscription_info={
            "properties": {
                "eventDeliverySchema": "EventGridSchema",
                "destination": {
                    "endpointType": "WebHook",
                    "properties": {
                        "endpointUrl": CALL_INBOUND_URL,
                        "maxEventsPerBatch": 1,
                    },
                },
                "filter": {
                    "enableAdvancedFilteringOnArrays": True,
                    "includedEventTypes": ["Microsoft.Communication.IncomingCall"],
                    "advancedFilters": [
                        {
                            "key": "data.to.PhoneNumber.Value",
                            "operatorType": "StringBeginsWith",
                            "values": [CONFIG.communication_service.phone_number],
                        }
                    ],
                },
            },
        },
    ).add_done_callback(callback)


def eventgrid_unregister() -> None:
    _logger.info(
        f"Deleting Event Grid subscription {eventgrid_subscription_name} (do not wait for completion)"
    )
    eventgrid_mgmt_client.system_topic_event_subscriptions.begin_delete(
        event_subscription_name=eventgrid_subscription_name,
        resource_group_name=CONFIG.eventgrid.resource_group,
        system_topic_name=CONFIG.eventgrid.system_topic,
    )


@api.get(
    "/health/liveness",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Liveness healthckeck, always returns 204, used to check if the API is up.",
)
async def health_liveness_get() -> None:
    return None


@api.get("/call/initiate", description="Initiate an outbound call to a phone number.")
def call_initiate_get(phone_number: str) -> None:
    _logger.info(f"Initiating outbound call to {phone_number}")
    target_caller = PhoneNumberIdentifier(phone_number)
    call_connection_properties = call_automation_client.create_call(
        callback_url=callback_url(phone_number),
        cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
        source_caller_id_number=source_caller,
        target_participant=target_caller,
    )
    _logger.info(
        f"Created call with connection id: {call_connection_properties.call_connection_id}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
                status_code=200,
            )

        elif event_type == SystemEventNames.AcsIncomingCallEventName:
            if event.data["from"]["kind"] == "phoneNumber":
                phone_number = event.data["from"]["phoneNumber"]["value"]
            else:
                phone_number = event.data["from"]["rawId"]

            _logger.debug(f"Incoming call handler caller ID: {phone_number}")
            call_context = event.data["incomingCallContext"]
            answer_call_result = call_automation_client.answer_call(
                callback_url=callback_url(phone_number),
                cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
                incoming_call_context=call_context,
            )
            _logger.info(
                f"Answered call with {phone_number} ({answer_call_result.call_connection_id})"
            )


@api.post(
    "/call/event/{call_id}",
    description="Handle callbacks from Azure Communication Services.",
    status_code=status.HTTP_204_NO_CONTENT,
)
# TODO: Secure this endpoint with a secret
# See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/secure-webhook-endpoint.md
async def call_event_post(request: Request, call_id: UUID) -> None:
    for event_dict in await request.json():
        event = CloudEvent.from_dict(event_dict)

        connection_id = event.data["callConnectionId"]
        operation_context = event.data.get("operationContext", None)
        client = call_automation_client.get_call_connection(
            call_connection_id=connection_id
        )
        call = get_call_by_id(call_id)
        target_caller = PhoneNumberIdentifier(call.phone_number)
        event_type = event.type

        _logger.debug(f"Call event received {event_type} for call {call}")
        _logger.debug(event.data)

        if event_type == "Microsoft.Communication.CallConnected":  # Call answered
            _logger.info(f"Call connected ({call.id})")
            call.recognition_retry = 0  # Reset recognition retry counter

            if not call.messages:  # First call
                await handle_recognize(
                    call=call,
                    client=client,
                    text=TTSPrompt.HELLO,
                    to=target_caller,
                )

            else:  # Returning call
                call.messages.append(
                    CallMessageModel(
                        content="Customer called again.", persona=CallPersona.HUMAN
                    )
                )
                await handle_play(
                    call=call,
                    client=client,
                    text=TTSPrompt.WELCOME_BACK,
                )
                await handle_media(
                    call=call,
                    client=client,
                    file="acknowledge.mp3",
                )
                await intelligence(call, client, target_caller)

        elif event_type == "Microsoft.Communication.CallDisconnected":  # Call hung up
            _logger.info(f"Call disconnected ({call.id})")
            await handle_hangup(call=call, client=client)

        elif (
            event_type == "Microsoft.Communication.RecognizeCompleted"
        ):  # Speech recognized
            if event.data["recognitionType"] == "speech":
                speech_text = event.data["speechResult"]["speech"]
                _logger.info(f"Recognition completed ({call.id}): {speech_text}")

                await handle_media(
                    call=call,
                    client=client,
                    file="acknowledge.mp3",
                )

                if speech_text is not None and len(speech_text) > 0:
                    call.messages.append(
                        CallMessageModel(content=speech_text, persona=CallPersona.HUMAN)
                    )
                    await intelligence(call, client, target_caller)

        elif (
            event_type == "Microsoft.Communication.RecognizeFailed"
        ):  # Speech recognition failed
            result_information = event.data["resultInformation"]
            error_code = result_information["subCode"]

            await handle_media(
                call=call,
                client=client,
                file="acknowledge.mp3",
            )

            # Error codes:
            # 8510 = Action failed, initial silence timeout reached
            # 8532 = Action failed, inter-digit silence timeout reached
            # 8512 = Unknown internal server error
            # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/recognize-action.md#event-codes
            if (
                error_code in (8510, 8532, 8512) and call.recognition_retry < 10
            ):  # Timeout retry
                await handle_recognize(
                    call=call,
                    client=client,
                    text=TTSPrompt.TIMEOUT_SILENCE,
                    to=target_caller,
                )
                call.recognition_retry += 1

            else:  # Timeout reached or other error
                await handle_play(
                    call=call,
                    client=client,
                    context=Context.GOODBYE,
                    text=TTSPrompt.GOODBYE,
                )

        elif event_type == "Microsoft.Communication.PlayCompleted":  # Media played
            _logger.debug(f"Play completed ({call.id})")

            if (
                operation_context == Context.TRANSFER_FAILED
                or operation_context == Context.GOODBYE
            ):  # Call ended
                _logger.info(f"Ending call ({call.id})")
                await handle_hangup(call=call, client=client)

            elif operation_context == Context.CONNECT_AGENT:  # Call transfer
                _logger.info(f"Initiating transfer call initiated ({call.id})")
                agent_caller = PhoneNumberIdentifier(CONFIG.workflow.agent_phone_number)
                client.transfer_call_to_participant(target_participant=agent_caller)

        elif event_type == "Microsoft.Communication.PlayFailed":  # Media play failed
            _logger.debug(f"Play failed ({call.id})")

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
                _logger.warn(
                    f"Error during media play, unknown error code {error_code}"
                )

        elif (
            event_type == "Microsoft.Communication.CallTransferAccepted"
        ):  # Call transfer accepted
            _logger.info(f"Call transfer accepted event ({call.id})")
            # TODO: Is there anything to do here?

        elif (
            event_type == "Microsoft.Communication.CallTransferFailed"
        ):  # Call transfer failed
            _logger.debig(f"Call transfer failed event ({call.id})")
            result_information = event.data["resultInformation"]
            sub_code = result_information["subCode"]
            _logger.info(f"Error during call transfer, subCode {sub_code} ({call.id})")
            await handle_play(
                call=call,
                client=client,
                context=Context.TRANSFER_FAILED,
                text=TTSPrompt.CALLTRANSFER_FAILURE,
            )

        save_call(call)


async def intelligence(
    call: CallModel, client: CallConnectionClient, target_caller: PhoneNumberIdentifier
) -> None:
    chat_res = await gpt_chat(call)
    _logger.info(f"Chat ({call.id}): {chat_res}")

    if chat_res.intent == IndentAction.TALK_TO_HUMAN:
        await handle_play(
            call=call,
            client=client,
            context=Context.CONNECT_AGENT,
            text=TTSPrompt.END_CALL_TO_CONNECT_AGENT,
        )

    elif chat_res.intent == IndentAction.END_CALL:
        await handle_play(
            call=call,
            client=client,
            context=Context.GOODBYE,
            text=TTSPrompt.GOODBYE,
        )

    elif chat_res.intent in (IndentAction.NEW_CLAIM, IndentAction.UPDATED_CLAIM, IndentAction.NEW_OR_UPDATED_REMINDER):
        await handle_play(
            call=call,
            client=client,
            store=False,
            text=chat_res.content,
        )
        await intelligence(call, client, target_caller)

    else:
        await handle_recognize(
            call=call,
            client=client,
            store=False,
            text=chat_res.content,
            to=target_caller,
        )


async def handle_play(
    client: CallConnectionClient,
    call: CallModel,
    text: str,
    context: Optional[str] = None,
    store: bool = True,
) -> None:
    """
    Play a text to a call participant.

    If store is True, the text will be stored in the call messages.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
    """
    if store:
        call.messages.append(
            CallMessageModel(content=text, persona=CallPersona.ASSISTANT)
        )

    try:
        client.play_media_to_all(
            play_source=audio_from_text(text), operation_context=context
        )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing ({call.id})")


async def gpt_completion(system: LLMPrompt, call: CallModel) -> str:
    _logger.debug(f"Running GPT completion ({call.id})")

    messages = [
        {
            "content": LLMPrompt.DEFAULT_SYSTEM.format(
                date=datetime.now().strftime("%A %d %B %Y %H:%M:%S"),
                phone_number=call.phone_number,
            ),
            "role": "system",
        },
        {
            "content": system.format(
                claim=call.claim.model_dump_json(),
                conversation=json.dumps(call.messages, default=pydantic_encoder),
                reminders=json.dumps(call.reminders, default=pydantic_encoder),
            ),
            "role": "system",
        },
    ]
    _logger.debug(f"Messages: {messages}")

    content = None
    try:
        res = await oai_gpt.chat.completions.create(
            max_tokens=1000,  # Arbitrary limit
            messages=messages,
            model=CONFIG.openai.gpt_model,
            temperature=0,  # Most focused and deterministic
        )
        content = res.choices[0].message.content

    except Exception:
        _logger.warn(f"OpenAI API call error", exc_info=True)

    return content or ""


async def gpt_chat(call: CallModel) -> ActionModel:
    _logger.debug(f"Running GPT chat ({call.id})")

    messages = [
        {
            "content": LLMPrompt.DEFAULT_SYSTEM.format(
                date=datetime.now().strftime("%A %d %B %Y %H:%M:%S"),
                phone_number=call.phone_number,
            ),
            "role": "system",
        },
        {
            "content": LLMPrompt.CHAT_SYSTEM.format(
                claim=call.claim.model_dump_json(),
                reminders=json.dumps(call.reminders, default=pydantic_encoder),
            ),
            "role": "system",
        },
    ]
    for message in call.messages:
        if message.persona == CallPersona.HUMAN:
            messages.append(
                {
                    "content": message.content,
                    "role": "user",
                }
            )
        elif message.persona == CallPersona.ASSISTANT:
            if not message.tool_calls:
                messages.append(
                    {
                        "content": message.content,
                        "role": "assistant",
                    }
                )
            else:
                messages.append(
                    {
                        "content": message.content,
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "arguments": tool_call.function_arguments,
                                    "name": tool_call.function_name,
                                },
                            }
                            for tool_call in message.tool_calls
                        ],
                    }
                )
                for tool_call in message.tool_calls:
                    messages.append(
                        {
                            "content": message.content,
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                        }
                    )
    _logger.debug(f"Messages: {messages}")

    customer_response_prop = "customer_response"
    tools = [
        {
            "type": "function",
            "function": {
                "description": "Use this if the user wants to talk to an agent and Assistant is unable to help, this will transfer the customer to an human agent.",
                "name": IndentAction.TALK_TO_HUMAN,
                "parameters": {
                    "properties": {},
                    "required": [],
                    "type": "object",
                },
            },
        },
        {
            "type": "function",
            "function": {
                "description": "Use this if the user wants to end the call, or if the user is satisfied with the answer and confirmed the end of the call.",
                "name": IndentAction.END_CALL,
                "parameters": {
                    "properties": {},
                    "required": [],
                    "type": "object",
                },
            },
        },
        {
            "type": "function",
            "function": {
                "description": "Use this if the user wants to create a new claim. This will reset the claim and reminder data. Old is stored but not accessible anymore. Double check with the user before using this.",
                "name": IndentAction.NEW_CLAIM,
                "parameters": {
                    "properties": {
                        f"{customer_response_prop}": {
                            "description": "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre', 'I am updating the policyholder contact info to 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
                            "type": "string",
                        }
                    },
                    "required": [
                        customer_response_prop,
                    ],
                    "type": "object",
                },
            },
        },
        {
            "type": "function",
            "function": {
                "description": "Use this if the user wants to update a claim field with a new value. Example: 'Update claim explanation to: I was driving on the highway when a car hit me from behind', 'Update policyholder contact info to: 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
                "name": IndentAction.UPDATED_CLAIM,
                "parameters": {
                    "properties": {
                        "field": {
                            "description": "The claim field to update.",
                            "enum": list(
                                ClaimModel.model_json_schema()["properties"].keys()
                            ),
                            "type": "string",
                        },
                        "value": {
                            "description": "The claim field value to update.",
                            "type": "string",
                        },
                        f"{customer_response_prop}": {
                            "description": "The text to be read to the customer to confirm the update. Only speak about this action. Use an imperative sentence. Example: 'I am updating the involved parties to Marie-Jeanne and Jean-Pierre', 'I am updating the policyholder contact info to 123 rue de la paix 75000 Paris, +33735119775, only call after 6pm'.",
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
        },
        {
            "type": "function",
            "function": {
                "description": "Use this if you think there is something important to do in the future, and you want to be reminded about it. If it already exists, it will be updated with the new values. Example: 'Remind Assitant thuesday at 10am to call back the customer', 'Remind Assitant next week to send the report', 'Remind the customer next week to send the documents by the end of the month'.",
                "name": IndentAction.NEW_OR_UPDATED_REMINDER,
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
                    ],
                    "type": "object",
                },
            },
        },
    ]
    _logger.debug(f"Tools: {tools}")

    try:
        # TODO: Manage to catch timeouts to limit waiting time for end users
        res = await oai_gpt.chat.completions.create(
            max_tokens=400,  # Communication Services limit is 400 characters for TTS, 400 tokens ~= 300 words
            messages=messages,
            model=CONFIG.openai.gpt_model,
            temperature=0,  # Most focused and deterministic
            tools=tools,
        )

        content = res.choices[0].message.content or ""
        tool_calls = res.choices[0].message.tool_calls

        _logger.debug(f"Chat response: {content}")
        _logger.debug(f"Tool calls: {tool_calls}")

        intent = IndentAction.CONTINUE
        models = []
        if tool_calls:
            # TODO: Catch tool error individually
            for tool_call in tool_calls:
                name = tool_call.function.name
                arguments = tool_call.function.arguments
                _logger.info(f"Tool call {name} with parameters {arguments}")

                model = CallToolModel(
                    content="",
                    function_arguments=arguments,
                    function_name=name,
                    id=tool_call.id,
                )

                if name == IndentAction.TALK_TO_HUMAN:
                    intent = IndentAction.TALK_TO_HUMAN

                elif name == IndentAction.END_CALL:
                    intent = IndentAction.END_CALL

                elif name == IndentAction.UPDATED_CLAIM:
                    intent = IndentAction.UPDATED_CLAIM
                    parameters = json.loads(arguments)
                    content += parameters[customer_response_prop] + " "
                    setattr(call.claim, parameters["field"], parameters["value"])
                    model.content = f"Updated claim field \"{parameters['field']}\" with value \"{parameters['value']}\"."

                elif name == IndentAction.NEW_CLAIM:
                    intent = IndentAction.NEW_CLAIM
                    parameters = json.loads(arguments)
                    content += parameters[customer_response_prop] + " "
                    call.claim = ClaimModel()
                    call.reminders = []
                    model.content = "Claim and reminders created reset."

                elif name == IndentAction.NEW_OR_UPDATED_REMINDER:
                    intent = IndentAction.NEW_OR_UPDATED_REMINDER
                    parameters = json.loads(arguments)
                    content += parameters[customer_response_prop] + " "

                    updated = False
                    for reminder in call.reminders:
                        if reminder.title == parameters["title"]:
                            reminder.description = parameters["description"]
                            reminder.due_date_time = parameters["due_date_time"]
                            model.content = (
                                f"Reminder \"{parameters['title']}\" updated."
                            )
                            updated = True
                            break

                    if not updated:
                        call.reminders.append(
                            ReminderModel(
                                description=parameters["description"],
                                due_date_time=parameters["due_date_time"],
                                title=parameters["title"],
                            )
                        )
                        model.content = f"Reminder \"{parameters['title']}\" created."

                models.append(model)

        call.messages.append(
            CallMessageModel(
                content=content,
                persona=CallPersona.ASSISTANT,
                tool_calls=models,
            )
        )

        return ActionModel(
            content=content,
            intent=intent,
        )

    except Exception:
        _logger.warn(f"OpenAI API call error", exc_info=True)

    return ActionModel(content=TTSPrompt.ERROR, intent=IndentAction.CONTINUE)


async def handle_recognize(
    client: CallConnectionClient,
    call: CallModel,
    to: PhoneNumberIdentifier,
    text: str,
    context: Optional[str] = None,
    store: bool = True,
) -> None:
    """
    Play a text to a call participant and start recognizing the response.

    If store is True, the text will be stored in the call messages.
    """
    if store:
        call.messages.append(
            CallMessageModel(content=text, persona=CallPersona.ASSISTANT)
        )

    # Split text in chunks of max 400 characters, separated by a comma
    chunks = []
    chunk = ""
    for word in text.split("."):  # Split by sentence
        to_add = f"{word}."
        if len(chunk) + len(to_add) >= 400:
            chunks.append(chunk)
            chunk = ""
        chunk += to_add
    if chunk:
        chunks.append(chunk)
    last_chunk = chunks.pop()

    try:
        # Play all chunks except the last one
        for chunk in chunks:
            _logger.debug(f"Playing chunk ({call.id}): {chunk}")
            await handle_play(
                call=call,
                client=client,
                text=chunk,
                store=False,
            )

        _logger.debug(f"Recognizing last chunk ({call.id}): {last_chunk}")
        # Play last chunk and start recognizing
        # TODO: Disable or lower profanity filter. The filter seems enabled by default, it replaces words like "holes in my roof" by "*** in my roof". This is not acceptable for a call center.
        client.start_recognizing_media(
            end_silence_timeout=3,  # Sometimes user includes breaks in their speech
            input_type=RecognizeInputType.SPEECH,
            operation_context=context,
            play_prompt=audio_from_text(last_chunk),
            speech_language=CONFIG.workflow.conversation_lang,
            target_participant=to,
        )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before recognizing ({call.id})")


async def handle_media(
    client: CallConnectionClient,
    call: CallModel,
    file: str,
    context: Optional[str] = None,
) -> None:
    try:
        client.play_media_to_all(
            play_source=FileSource(f"{CONFIG.resources.public_url}/{file}"),
            operation_context=context,
        )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing ({call.id})")


async def handle_hangup(client: CallConnectionClient, call: CallModel) -> None:
    _logger.debug(f"Hanging up call ({call.id})")
    try:
        client.hang_up(is_for_everyone=True)
    except ResourceNotFoundError:
        _logger.debug(f"Call already hung up ({call.id})")

    content = await gpt_completion(LLMPrompt.SMS_SUMMARY_SYSTEM, call)
    _logger.info(f"SMS report ({call.id}): {content}")

    try:
        responses = sms_client.send(
            from_=CONFIG.communication_service.phone_number,
            message=content,
            to=call.phone_number,
        )
        response = responses[0]

        if (response.successful):
            _logger.info(f"SMS report sent {response.message_id} to {response.to} ({call.id})")
        else:
            _logger.warn(f"Failed SMS to {response.to}, status {response.http_status_code}, error {response.error_message} ({call.id})")

    except Exception:
        _logger.warn(f"SMS error ({call.id})", exc_info=True)


def audio_from_text(text: str) -> TextSource:
    if len(text) > 400:
        _logger.warning(
            f"Text is too long to be processed by TTS, truncating to 400 characters, fix this!"
        )
        text = text[:400]
    return TextSource(
        source_locale=CONFIG.workflow.conversation_lang,
        text=text,
        voice_name=CONFIG.communication_service.voice_name,
    )


def callback_url(caller_id: str) -> str:
    """
    Generate the callback URL for a call.

    If the caller has already called, use the same call ID, to keep the conversation history. Otherwise, create a new call ID.
    """
    call = get_last_call_by_phone_number(caller_id)
    if not call:
        call = CallModel(phone_number=caller_id)
        save_call(call)
    return f"{CALL_EVENT_URL}/{call.id}"


def init_db():
    db.execute(
        "CREATE TABLE IF NOT EXISTS calls (id VARCHAR(32) PRIMARY KEY, phone_number TEXT, data TEXT, created_at TEXT)"
    )
    db.commit()


def save_call(call: CallModel):
    db.execute(
        "INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?)",
        (
            call.id.hex,  # id
            call.phone_number,  # phone_number
            call.model_dump_json(),  # data
            call.created_at.isoformat(),  # created_at
        ),
    )
    db.commit()


def get_call_by_id(call_id: UUID) -> CallModel:
    cursor = db.execute(
        "SELECT data FROM calls WHERE id = ?",
        (call_id.hex,),
    )
    row = cursor.fetchone()
    return CallModel.model_validate_json(row[0]) if row else None


def get_last_call_by_phone_number(phone_number: str) -> Optional[CallModel]:
    cursor = db.execute(
        f"SELECT data FROM calls WHERE phone_number = ? AND DATETIME(created_at) > DATETIME('now', '-{CONFIG.workflow.conversation_timeout_hour} hours') ORDER BY created_at DESC LIMIT 1",
        (phone_number,),
    )
    row = cursor.fetchone()
    return CallModel.model_validate_json(row[0]) if row else None
