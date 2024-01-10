from typing import Optional, Union
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
from helpers.version import VERSION
from models.action import ActionModel, Indent as IndentAction
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
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
import re
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
# TODO: Wait for the legal approval to send SMS with Azure Communication Services
# sms_client = SmsClient(credential=AZ_CREDENTIAL, endpoint=CONFIG.communication_service.endpoint)
twilio_client = Client(
    CONFIG.twilio.account_sid, CONFIG.twilio.auth_token.get_secret_value()
)
db = sqlite3.connect(".local.sqlite", check_same_thread=False)

EVENTS_DOMAIN = environ.get("EVENTS_DOMAIN").strip("/")
assert EVENTS_DOMAIN, "EVENTS_DOMAIN environment variable is not set"
CALL_EVENT_URL = f"{EVENTS_DOMAIN}/call/event"
CALL_INBOUND_URL = f"{EVENTS_DOMAIN}/call/inbound"

DEFAULT_SYSTEM_PROMPT = f"""
    Assistant called {CONFIG.workflow.bot_name} and is in a call center for the insurance company {CONFIG.workflow.bot_company} as an expert with 20 years of experience. Today is {{date}}. Customer is calling from {{phone_number}}. Call center number is {CONFIG.communication_service.phone_number}.
"""
CHAT_SYSTEM_PROMPT = f"""
    Assistant will help the customer with their insurance claim.

    Assistant:
    - Answers in {CONFIG.workflow.conversation_lang}, even if the customer speaks in English
    - Ask the customer to repeat or rephrase their question if it is not clear
    - Cannot talk about any topic other than insurance claims
    - Do not ask the customer more than 2 questions in a row
    - Explain the tools (called actions for the customer) you used
    - If user called multiple times, continue the discussion from the previous call
    - Is polite, helpful, and professional
    - Keep the sentences short and simple
    - Refer customers to emergency services or the police if necessary, but cannot give advice under any circumstances
    - Rephrase the customer's questions as statements and answer them

    Assistant requires data from the customer to fill the claim. Latest claim data will be given. Assistant role is not over until all the relevant data is gathered.
"""
SMS_SUMMARY_SYSTEM_PROMPT = f"""
    Assistant will summarize the call with the customer in a single SMS. The customer cannot reply to this SMS.

    Assistant:
    - Answers in {CONFIG.workflow.conversation_lang}, even if the customer speaks in English
    - Briefly summarize the call with the customer
    - Can include personal details about the customer
    - Cannot talk about any topic other than insurance claims
    - Do not prefix the answer with any text, like "The answer is" or "Summary of the call"
    - Include salutations at the end of the SMS
    - Incude details stored in the claim, to make the customer confident that the situation is understood
    - Is polite, helpful, and professional
    - Refer to the customer by their name, if known
    - Use simple and short sentences

    Conversation history:
    {{conversation}}
"""

AGENT_PHONE_NUMBER_EMPTY_PROMPT = "Je suis désolé, mais nous enregistrons actuellement un nombre élevé d'appels et tous nos agents sont actuellement occupés. Notre prochain agent disponible vous rappellera dès que possible."
CALLTRANSFER_FAILURE_PROMPT = "Il semble que je ne puisse pas vous mettre en relation avec un agent pour l'instant, mais le prochain agent disponible vous rappellera dès que possible."
CONNECT_AGENT_PROMPT = "Je suis désolé, je n'ai pas été en mesure de répondre à votre demande. Permettez-moi de vous transférer à un agent qui pourra vous aider davantage. Veuillez rester en ligne et je vous recontacterai sous peu."
END_CALL_TO_CONNECT_AGENT_PROMPT = (
    "Bien sûr, restez en ligne. Je vais vous transférer à un agent."
)
ERROR_PROMPT = (
    "Je suis désolé, j'ai rencontré une erreur. Pouvez-vous répéter votre demande ?"
)
GOODBYE_PROMPT = f"Merci de votre appel, j'espère avoir pu vous aider. N'hésitez pas à rappeler, j'ai tout mémorisé. {CONFIG.workflow.bot_company} vous souhaite une excellente journée !"
HELLO_PROMPT = f"Bonjour, je suis {CONFIG.workflow.bot_name}, l'assistant {CONFIG.workflow.bot_company} ! Je suis spécialiste des sinistres. Lorsque vous entendrez un bip, c'est que je travaille. Comment puis-je vous aider ?"
TIMEOUT_SILENCE_PROMPT = "Je suis désolé, je n'ai rien entendu. Si vous avez besoin d'aide, dites-moi comment je peux vous aider."
UPDATED_CLAIM_PROMPT = "Je mets à jour votre dossier..."
WELCOME_BACK_PROMPT = f"Bonjour, je suis {CONFIG.workflow.bot_name}, l'assistant {CONFIG.workflow.bot_company} ! Je vois que vous avez déjà appelé il y a moins de {CONFIG.workflow.conversation_timeout_hour} heures. Lorsque vous entendrez un bip, c'est que je travaille. Laissez-moi quelques secondes pour récupérer votre dossier..."


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
                call.messages.append(
                    CallMessageModel(content=HELLO_PROMPT, persona=CallPersona.ASSISTANT)
                )
                await handle_recognize(
                    call=call,
                    client=client,
                    text=HELLO_PROMPT,
                    to=target_caller,
                )

            else: # Returning call
                call.messages.append(
                    CallMessageModel(content="Customer called again.", persona=CallPersona.HUMAN)
                )
                call.messages.append(
                    CallMessageModel(content=WELCOME_BACK_PROMPT, persona=CallPersona.ASSISTANT)
                )
                await handle_play(
                    call=call,
                    client=client,
                    text=WELCOME_BACK_PROMPT,
                )
                await handle_media(
                    call=call,
                    client=client,
                    file="acknowledge.wav",
                )
                await intelligence(call, client, target_caller)

        elif event_type == "Microsoft.Communication.CallDisconnected":  # Call hung up
            _logger.info(f"Call disconnected ({call.id})")
            await handle_hangup(call=call, client=client)

        elif event_type == "Microsoft.Communication.RecognizeCompleted":  # Speech recognized
            if event.data["recognitionType"] == "speech":
                speech_text = event.data["speechResult"]["speech"]
                _logger.info(f"Recognition completed ({call.id}): {speech_text}")

                await handle_media(
                    call=call,
                    client=client,
                    file="acknowledge.wav",
                )

                if speech_text is not None and len(speech_text) > 0:
                    call.messages.append(
                        CallMessageModel(content=speech_text, persona=CallPersona.HUMAN)
                    )
                    await intelligence(call, client, target_caller)

        elif event_type == "Microsoft.Communication.RecognizeFailed":  # Speech recognition failed
            result_information = event.data["resultInformation"]
            error_code = result_information["subCode"]

            await handle_media(
                call=call,
                client=client,
                file="acknowledge.wav",
            )

            if error_code == 8510 and call.recognition_retry < 10:  # Timeout retry
                call.messages.append(
                    CallMessageModel(content=TIMEOUT_SILENCE_PROMPT, persona=CallPersona.ASSISTANT)
                )
                await handle_recognize(
                    call=call,
                    client=client,
                    text=TIMEOUT_SILENCE_PROMPT,
                    to=target_caller,
                )
                call.recognition_retry += 1

            else:  # Timeout reached or other error
                await handle_play(
                    call=call,
                    client=client,
                    context=Context.GOODBYE.value,
                    text=GOODBYE_PROMPT,
                )

        elif event_type == "Microsoft.Communication.PlayCompleted":  # Media played
            _logger.debug(f"Play completed ({call.id})")

            if (
                operation_context == Context.TRANSFER_FAILED.value
                or operation_context == Context.GOODBYE.value
            ):  # Call ended
                _logger.info(f"Ending call ({call.id})")
                await handle_hangup(call=call, client=client)

            elif operation_context == Context.CONNECT_AGENT.value:  # Call transfer
                _logger.info(f"Initiating transfer call initiated ({call.id})")
                agent_caller = PhoneNumberIdentifier(CONFIG.workflow.agent_phone_number)
                client.transfer_call_to_participant(target_participant=agent_caller)

        elif event_type == "Microsoft.Communication.CallTransferAccepted":  # Call transfer accepted
            _logger.info(f"Call transfer accepted event ({call.id})")
            # TODO: Is there anything to do here?

        elif event_type == "Microsoft.Communication.CallTransferFailed":  # Call transfer failed
            _logger.debig(f"Call transfer failed event ({call.id})")
            result_information = event.data["resultInformation"]
            sub_code = result_information["subCode"]
            _logger.info(f"Error during call transfer, subCode {sub_code} ({call.id})")
            await handle_play(
                call=call,
                client=client,
                context=Context.TRANSFER_FAILED.value,
                text=CALLTRANSFER_FAILURE_PROMPT,
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
            context=Context.CONNECT_AGENT.value,
            text=END_CALL_TO_CONNECT_AGENT_PROMPT,
        )

    elif chat_res.intent == IndentAction.END_CALL:
        await handle_play(
            call=call,
            client=client,
            context=Context.GOODBYE.value,
            text=GOODBYE_PROMPT,
        )

    elif chat_res.intent == IndentAction.UPDATE_CLAIM:
        await handle_play(
            call=call,
            client=client,
            text=UPDATED_CLAIM_PROMPT,
        )
        await intelligence(call, client, target_caller)

    else:
        await handle_recognize(
            call=call,
            client=client,
            text=chat_res.content,
            to=target_caller,
        )


async def handle_play(
    client: CallConnectionClient,
    call: CallModel,
    text: str,
    context: Optional[str] = None,
) -> None:
    """
    Play a text to a call participant.

    See: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts
    """
    call.messages.append(CallMessageModel(content=text, persona=CallPersona.ASSISTANT))
    try:
        client.play_media_to_all(
            play_source=audio_from_text(text), operation_context=context
        )
    except ResourceNotFoundError:
        _logger.debug(f"Call hung up before playing ({call.id})")


async def gpt_completion(system: str, call: CallModel) -> str:
    _logger.debug(f"Running GPT completion ({call.id})")

    messages = [
        {
            "content": DEFAULT_SYSTEM_PROMPT.format(
                date=datetime.now().strftime("%A %d %B %Y %H:%M:%S"),
                phone_number=call.phone_number,
            ),
            "role": "system",
        },
        {
            "content": system.format(
                conversation=str(call.messages),
            ),
            "role": "system",
        },
        {
            "content": f"Claim status is: {call.claim.model_dump_json()}",
            "role": "assistant",
        },
    ]
    _logger.debug(f"Messages: {messages}")

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
            "content": DEFAULT_SYSTEM_PROMPT.format(
                date=datetime.now().strftime("%A %d %B %Y %H:%M:%S"),
                phone_number=call.phone_number,
            ),
            "role": "system",
        },
        {
            "content": CHAT_SYSTEM_PROMPT,
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
    messages.append(
        {
            "content": f"Claim status is: {call.claim.model_dump_json()}",
            "role": "assistant",
        }
    )
    _logger.debug(f"Messages: {messages}")

    tools = [
        {
            "type": "function",
            "function": {
                "description": "Use this if the user wants to talk to an agent and Assistant is unable to help, this will transfer the customer to an human agent.",
                "name": IndentAction.TALK_TO_HUMAN.value,
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
                "name": IndentAction.END_CALL.value,
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
                "description": "Use this if the user wants to update a claim field with a new value.",
                "name": IndentAction.UPDATE_CLAIM.value,
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
                    },
                    "required": [
                        "field",
                        "value",
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
            max_tokens=150,  # Communication Services limit is 400 characters for TTS
            messages=messages,
            model=CONFIG.openai.gpt_model,
            temperature=0,  # Most focused and deterministic
            tools=tools,
        )

        content = res.choices[0].message.content
        tool_calls = res.choices[0].message.tool_calls

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

                if name == IndentAction.TALK_TO_HUMAN.value:
                    intent = IndentAction.TALK_TO_HUMAN
                elif name == IndentAction.END_CALL.value:
                    intent = IndentAction.END_CALL
                elif name == IndentAction.UPDATE_CLAIM.value:
                    intent = IndentAction.UPDATE_CLAIM
                    parameters = json.loads(arguments)
                    setattr(call.claim, parameters["field"], parameters["value"])
                    model.content = f"Udated claim field {parameters['field']} with value {parameters['value']}"

                models.append(model)

        call.messages.append(
            CallMessageModel(
                content=content or "",
                persona=CallPersona.ASSISTANT,
                tool_calls=models,
            )
        )

        return ActionModel(
            content=content or "",
            intent=intent,
        )

    except Exception:
        _logger.warn(f"OpenAI API call error", exc_info=True)

    return ActionModel(content=ERROR_PROMPT, intent=IndentAction.CONTINUE)


async def handle_recognize(
    client: CallConnectionClient,
    call: CallModel,
    to: PhoneNumberIdentifier,
    text: str,
    context: Optional[str] = None,
) -> None:
    try:
        client.start_recognizing_media(
            end_silence_timeout=3,  # Sometimes user includes breaks in their speech
            input_type=RecognizeInputType.SPEECH,
            operation_context=context,
            play_prompt=audio_from_text(text),
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

    content = await gpt_completion(SMS_SUMMARY_SYSTEM_PROMPT, call)
    _logger.info(f"SMS report ({call.id}): {content}")

    try:
        # TODO: Wait for the legal approval to send SMS with Azure Communication Services
        # sms_client.send(
        #     from_=CONFIG.communication_service.phone_number,
        #     message=content,
        #     to=call.phone_number,
        # )
        twilio_client.messages.create(
            body=content,
            from_=CONFIG.twilio.phone_number,
            to=call.phone_number,
        )
        _logger.info(f"SMS report sent ({call.id})")
    except TwilioRestException:
        _logger.warn(f"Twilio SMS error ({call.id})", exc_info=True)


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
    db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, phone_number TEXT, data TEXT, created_at TEXT)")
    db.commit()


def save_call(call: CallModel):
    db.execute(
        "INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?)",
        (
            str(call.id),  # id
            call.phone_number,  # phone_number
            call.model_dump_json(),  # data
            call.created_at.isoformat(),  # created_at
        ),
    )
    db.commit()


def get_call_by_id(call_id: UUID) -> CallModel:
    cursor = db.execute(
        "SELECT data FROM calls WHERE id = ?",
        (str(call_id),),
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
