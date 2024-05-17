# First imports, to make sure the following logs are first
from helpers.logging import build_logger
from helpers.config import CONFIG


_logger = build_logger(__name__)
_logger.info(f"call-center-ai v{CONFIG.version}")


# General imports
from typing import (
    Any,
    Literal,
    Optional,
    Union,
)
from azure.communication.callautomation import (
    CallAutomationClient,
    PhoneNumberIdentifier,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.messaging import CloudEvent
from azure.eventgrid import EventGridEvent, SystemEventNames
from fastapi import (
    BackgroundTasks,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader
from models.call import CallStateModel, CallGetModel
from models.next import ActionEnum as NextActionEnum
from urllib.parse import quote_plus, urljoin
import asyncio
from uuid import UUID
import mistune
from helpers.pydantic_types.phone_numbers import PhoneNumber
from twilio.twiml.messaging_response import MessagingResponse
from helpers.call_events import (
    on_call_connected,
    on_call_disconnected,
    on_ivr_recognized,
    on_new_call,
    on_play_completed,
    on_play_error,
    on_sms_received,
    on_speech_recognized,
    on_speech_timeout_error,
    on_speech_unknown_error,
    on_transfer_completed,
    on_transfer_error,
)
from models.readiness import ReadinessModel, ReadinessCheckModel, ReadinessStatus
from htmlmin.minify import html_minify


# Jinja configuration
_jinja = Environment(
    autoescape=True,
    enable_async=True,
    loader=FileSystemLoader("public_website"),
    optimized=False,  # Outsource optimization to html_minify
)
# Jinja custom functions
_jinja.filters["quote_plus"] = lambda x: quote_plus(str(x)) if x else ""
_jinja.filters["markdown"] = lambda x: mistune.create_markdown(plugins=["abbr", "speedup", "url"])(x) if x else ""  # type: ignore

# Azure Communication Services
_source_caller = PhoneNumberIdentifier(CONFIG.communication_service.phone_number)
_logger.info(f"Using phone number {str(CONFIG.communication_service.phone_number)}")
# Cannot place calls with RBAC, need to use access key (see: https://learn.microsoft.com/en-us/azure/communication-services/concepts/authentication#authentication-options)
_automation_client = CallAutomationClient(
    endpoint=CONFIG.communication_service.endpoint,
    credential=AzureKeyCredential(
        CONFIG.communication_service.access_key.get_secret_value()
    ),
)

# Persistences
_cache = CONFIG.cache.instance()
_db = CONFIG.database.instance()
_search = CONFIG.ai_search.instance()
_sms = CONFIG.sms.instance()

# FastAPI
_logger.info(f'Using root path "{CONFIG.api.root_path}"')
api = FastAPI(
    contact={
        "url": "https://github.com/clemlesne/call-center-ai",
    },
    description="AI-powered call center solution with Azure and OpenAI GPT.",
    license_info={
        "name": "Apache-2.0",
        "url": "https://github.com/clemlesne/call-center-ai/blob/master/LICENCE",
    },
    root_path=CONFIG.api.root_path,
    title="call-center-ai",
    version=CONFIG.version,
)


assert CONFIG.api.events_domain, "api.events_domain config is not set"
_COMMUNICATIONSERVICES_EVENT_TPL = urljoin(
    str(CONFIG.api.events_domain),
    "/communicationservices/event/{call_id}/{callback_secret}",
)
_logger.info(f"Using call event URL {_COMMUNICATIONSERVICES_EVENT_TPL}")


@api.get(
    "/health/liveness",
    status_code=status.HTTP_204_NO_CONTENT,
    description="Liveness healthckeck, always returns 204, used to check if the API is up.",
    name="Get liveness",
)
async def health_liveness_get() -> None:
    pass


@api.get(
    "/health/readiness",
    description="Readiness healthckeck, returns the status of all components, and fails if one of them is not ready. If all components are ready, returns 200, otherwise 503.",
)
async def health_readiness_get() -> JSONResponse:
    # Check all components in parallel
    cache_check, db_check, search_check, sms_check = await asyncio.gather(
        _cache.areadiness(), _db.areadiness(), _search.areadiness(), _sms.areadiness()
    )
    readiness = ReadinessModel(
        status=ReadinessStatus.OK,
        checks=[
            ReadinessCheckModel(id="cache", status=cache_check),
            ReadinessCheckModel(id="index", status=db_check),
            ReadinessCheckModel(id="startup", status=ReadinessStatus.OK),
            ReadinessCheckModel(id="store", status=search_check),
            ReadinessCheckModel(id="stream", status=sms_check),
        ],
    )
    # If one of the checks fails, the whole readiness fails
    status_code = status.HTTP_200_OK
    for check in readiness.checks:
        if check.status != ReadinessStatus.OK:
            readiness.status = ReadinessStatus.FAIL
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            break
    return JSONResponse(
        content=readiness.model_dump(mode="json"),
        status_code=status_code,
    )


@api.get(
    "/report",
    description="Display the calls history for all phone numbers in a web page.",
    name="Search for reports (browser)",
)
async def report_get(
    phone_number: Optional[Union[PhoneNumber, Literal[""]]] = None,
) -> HTMLResponse:
    count = 100
    calls, total = (
        await _db.call_asearch_all(
            count=count,
            phone_number=phone_number or None,
        )
        or []
    )
    template = _jinja.get_template("list.html.jinja")
    render = await template.render_async(
        application_insights_connection_string=CONFIG.monitoring.application_insights.connection_string.get_secret_value(),
        calls=calls or [],
        count=count,
        phone_number=phone_number,
        total=total,
        version=CONFIG.version,
    )
    render = html_minify(render)  # Minify HTML
    return HTMLResponse(content=render)


@api.get(
    "/report/{call_id}",
    description="Display the call report in a web page.",
    name="Get a report (browser)",
)
async def report_single_get(call_id: UUID) -> HTMLResponse:
    call = await _db.call_aget(call_id)
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call {call_id} not found",
        )
    template = _jinja.get_template("single.html.jinja")
    render = await template.render_async(
        application_insights_connection_string=CONFIG.monitoring.application_insights.connection_string.get_secret_value(),
        bot_company=CONFIG.workflow.bot_company,
        bot_name=CONFIG.workflow.bot_name,
        call=call,
        next_actions=[action for action in NextActionEnum],
        version=CONFIG.version,
    )
    render = html_minify(render)  # Minify HTML
    return HTMLResponse(content=render)


# TODO: Add total (int) and calls (list) as a wrapper for the list of calls
@api.get(
    "/call",
    description="Search all calls by phone number.",
    name="Search calls",
)
async def call_search_get(phone_number: PhoneNumber) -> list[CallGetModel]:
    calls, _ = await _db.call_asearch_all(phone_number=phone_number, count=1)
    output = [CallGetModel.model_validate(call) for call in calls or []]
    return output


@api.get(
    "/call/{call_id}",
    description="Get a call by its ID.",
    name="Get call",
)
async def call_get(call_id: UUID) -> CallGetModel:
    call = await _db.call_aget(call_id)
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call {call_id} not found",
        )
    return CallGetModel.model_validate(call)


@api.post(
    "/call",
    description="Initiate a call to a phone number.",
    name="Create call",
)
async def call_post(phone_number: PhoneNumber) -> CallGetModel:
    url, call = await _communicationservices_event_url(phone_number)
    call_connection_properties = _automation_client.create_call(
        callback_url=url,
        cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
        source_caller_id_number=_source_caller,
        target_participant=PhoneNumberIdentifier(phone_number),  # type: ignore
    )
    _logger.info(
        f"Created call with connection id: {call_connection_properties.call_connection_id}"
    )
    return CallGetModel.model_validate(call)


# TODO: Secure this endpoint with a secret, either in the Authorization header or in the URL
@api.post(
    "/eventgrid/event",
    description="Handle incoming call from a Azure Event Grid event originating from Azure Communication Services.",
    name="Receive Event Grid event",
)
async def eventgrid_event_post(
    background_tasks: BackgroundTasks,
    request: Request,
) -> Response:
    responses = await asyncio.gather(
        *[
            _eventgrid_event_worker(
                background_tasks=background_tasks,
                event_dict=event_dict,
            )
            for event_dict in await request.json()
        ]
    )
    for response in responses:
        if response:
            return response
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _eventgrid_event_worker(
    background_tasks: BackgroundTasks,
    event_dict: dict[str, Any],
) -> Optional[Union[JSONResponse, Response]]:
    event = EventGridEvent.from_dict(event_dict)
    event_type = event.event_type

    _logger.debug(f"Call inbound event {event_type} with data {event.data}")

    if event_type == SystemEventNames.EventGridSubscriptionValidationEventName:
        validation_code: str = event.data["validationCode"]
        _logger.info(f"Validating Event Grid subscription ({validation_code})")
        return JSONResponse(
            content={"validationResponse": event.data["validationCode"]},
            status_code=status.HTTP_200_OK,
        )

    elif event_type == SystemEventNames.AcsIncomingCallEventName:
        call_context: str = event.data["incomingCallContext"]
        phone_number = event.data["from"]["phoneNumber"]["value"]
        phone_number = PhoneNumber(phone_number)
        url, _ = await _communicationservices_event_url(phone_number)
        event_status = await on_new_call(
            callback_url=url,
            client=_automation_client,
            context=call_context,
            phone_number=phone_number,
        )
        if not event_status:
            return Response(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    elif event_type == SystemEventNames.AcsSmsReceivedEventName:
        message: str = event.data["message"]
        phone_number: str = event.data["from"]
        call = await _db.call_asearch_one(phone_number)
        if not call:
            _logger.warning(f"Call for phone number {phone_number} not found")
            return Response(
                status_code=status.HTTP_404_NOT_FOUND,
            )
        event_status = await on_sms_received(
            background_tasks=background_tasks,
            call=call,
            client=_automation_client,
            message=message,
        )
        if not event_status:
            return Response(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@api.post(
    "/communicationservices/event/{call_id}/{secret}",
    description="Handle callbacks from Azure Communication Services.",
    name="Receive Communication Services event",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def communicationservices_event_post(
    request: Request,
    background_tasks: BackgroundTasks,
    call_id: UUID,
    secret: str,
) -> None:
    await asyncio.gather(
        *[
            _communicationservices_event_worker(
                background_tasks, event_dict, call_id, secret
            )
            for event_dict in await request.json()
        ]
    )


async def _communicationservices_event_worker(
    background_tasks: BackgroundTasks,
    event_dict: dict,
    call_id: UUID,
    secret: str,
) -> None:
    call = await _db.call_aget(call_id)
    if not call:
        _logger.warning(f"Call {call_id} not found")
        return
    if call.callback_secret != secret:
        _logger.warning(f"Secret for call {call_id} does not match")
        return

    event = CloudEvent.from_dict(event_dict)
    assert isinstance(event.data, dict)

    # Store connection ID
    connection_id = event.data["callConnectionId"]
    call.voice_id = connection_id

    # Extract event context
    operation_context = event.data.get("operationContext", None)
    event_type = event.type

    _logger.debug(f"Call event received {event_type} for call {call}")
    _logger.debug(event.data)

    if event_type == "Microsoft.Communication.CallConnected":  # Call answered
        await on_call_connected(
            call=call,
            client=_automation_client,
        )

    elif event_type == "Microsoft.Communication.CallDisconnected":  # Call hung up
        await on_call_disconnected(
            background_tasks=background_tasks,
            call=call,
            client=_automation_client,
        )

    elif (
        event_type == "Microsoft.Communication.RecognizeCompleted"
    ):  # Speech recognized
        recognition_result: str = event.data["recognitionType"]

        if recognition_result == "speech":  # Handle voice
            speech_text: str = event.data["speechResult"]["speech"]
            await on_speech_recognized(
                background_tasks=background_tasks,
                call=call,
                client=_automation_client,
                text=speech_text,
            )

        elif recognition_result == "choices":  # Handle IVR
            label_detected: str = event.data["choiceResult"]["label"]
            await on_ivr_recognized(
                background_tasks=background_tasks,
                call=call,
                client=_automation_client,
                label=label_detected,
            )

    elif (
        event_type == "Microsoft.Communication.RecognizeFailed"
    ):  # Speech recognition failed
        result_information = event.data["resultInformation"]
        error_code: int = result_information["subCode"]

        # Error codes:
        # 8510 = Action failed, initial silence timeout reached
        # 8532 = Action failed, inter-digit silence timeout reached
        # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/recognize-action.md#event-codes
        if error_code in (8510, 8532):  # Timeout retry
            await on_speech_timeout_error(
                call=call,
                client=_automation_client,
            )
        else:  # Unknown error
            await on_speech_unknown_error(
                call=call,
                client=_automation_client,
                error_code=error_code,
            )

    elif event_type == "Microsoft.Communication.PlayCompleted":  # Media played
        await on_play_completed(
            background_tasks=background_tasks,
            call=call,
            client=_automation_client,
            context=operation_context,
        )

    elif event_type == "Microsoft.Communication.PlayFailed":  # Media play failed
        result_information = event.data["resultInformation"]
        error_code: int = result_information["subCode"]
        await on_play_error(error_code)

    elif (
        event_type == "Microsoft.Communication.CallTransferAccepted"
    ):  # Call transfer accepted
        await on_transfer_completed()

    elif (
        event_type == "Microsoft.Communication.CallTransferFailed"
    ):  # Call transfer failed
        result_information = event.data["resultInformation"]
        sub_code: int = result_information["subCode"]
        await on_transfer_error(
            call=call,
            client=_automation_client,
            error_code=sub_code,
        )

    await _db.call_aset(
        call
    )  # TODO: Do not persist on every event, this is simpler but not efficient


async def _communicationservices_event_url(
    phone_number: PhoneNumber,
) -> tuple[str, CallStateModel]:
    """
    Generate the callback URL for a call.

    If the caller has already called, use the same call ID, to keep the conversation history. Otherwise, create a new call ID.
    """
    call = await _db.call_asearch_one(phone_number)
    if not call:
        call = CallStateModel(phone_number=phone_number)
        await _db.call_aset(call)  # Create for the first time
    url = _COMMUNICATIONSERVICES_EVENT_TPL.format(
        callback_secret=call.callback_secret,
        call_id=str(call.call_id),
    )
    return url, call


# TODO: Secure this endpoint with a secret, either in the Authorization header or in the URL
@api.post(
    "/twilio/sms",
    description="Handle incoming SMS event from Twilio.",
    name="Receive Twilio SMS event",
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/xml": {
                    "example": str(MessagingResponse()),
                },
            },
        },
    },
)
async def twilio_sms(
    background_tasks: BackgroundTasks,
    Body: str = Form(alias="Body"),
    From: str = Form(alias="From"),
) -> Response:
    """
    Handle incoming SMS event from Twilio.
    """
    phone_number = PhoneNumber(From)
    call = await _db.call_asearch_one(phone_number)
    if not call:
        _logger.warning(f"Call for phone number {phone_number} not found")
    else:
        event_status = await on_sms_received(
            call=call,
            message=Body,
            client=_automation_client,
            background_tasks=background_tasks,
        )
        if not event_status:
            return Response(
                background=background_tasks,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    return Response(
        background=background_tasks,
        content=str(MessagingResponse()),  # Twilio expects an empty response everytime
        media_type="application/xml",
    )
