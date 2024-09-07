import asyncio
import json
from datetime import timedelta
from http import HTTPStatus
from os import getenv
from urllib.parse import quote_plus, urljoin
from uuid import UUID

import azure.functions as func
import jwt
import mistune
from azure.communication.callautomation import PhoneNumberIdentifier
from azure.communication.callautomation.aio import CallAutomationClient
from azure.core.credentials import AzureKeyCredential
from azure.core.messaging import CloudEvent
from azure.eventgrid import EventGridEvent, SystemEventNames
from htmlmin.minify import html_minify
from jinja2 import Environment, FileSystemLoader
from pydantic import TypeAdapter, ValidationError
from twilio.twiml.messaging_response import MessagingResponse

from helpers.call_events import (
    on_call_connected,
    on_call_disconnected,
    on_end_call,
    on_ivr_recognized,
    on_new_call,
    on_play_completed,
    on_play_error,
    on_recognize_timeout_error,
    on_recognize_unknown_error,
    on_sms_received,
    on_speech_recognized,
    on_transfer_completed,
    on_transfer_error,
)
from helpers.call_utils import ContextEnum as CallContextEnum
from helpers.config import CONFIG
from helpers.http import azure_transport
from helpers.logging import logger
from helpers.monitoring import CallAttributes, span_attribute, tracer
from helpers.pydantic_types.phone_numbers import PhoneNumber
from helpers.resources import resources_dir
from models.call import CallGetModel, CallInitiateModel, CallStateModel
from models.next import ActionEnum as NextActionEnum
from models.readiness import ReadinessCheckModel, ReadinessEnum, ReadinessModel

logger.info(
    "call-center-ai v%s (Azure Functions v%s)",
    CONFIG.version,
    getattr(func, "__version__"),
)

# Jinja configuration
_jinja = Environment(
    autoescape=True,
    enable_async=True,
    loader=FileSystemLoader("public_website"),
    optimized=False,  # Outsource optimization to html_minify
)
# Jinja custom functions
_jinja.filters["quote_plus"] = lambda x: quote_plus(str(x)) if x else ""
_jinja.filters["markdown"] = lambda x: (
    mistune.create_markdown(plugins=["abbr", "speedup", "url"])(x) if x else ""
)  # pyright: ignore

# Azure Communication Services
_automation_client: CallAutomationClient | None = None
_source_caller = PhoneNumberIdentifier(CONFIG.communication_services.phone_number)
logger.info("Using phone number %s", CONFIG.communication_services.phone_number)
_communication_services_jwks_client = jwt.PyJWKClient(
    cache_keys=True,
    uri="https://acscallautomation.communication.azure.com/calling/keys",
)

# Persistences
_cache = CONFIG.cache.instance()
_db = CONFIG.database.instance()
_search = CONFIG.ai_search.instance()
_sms = CONFIG.sms.instance()

# Azure Functions
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Communication Services callback
assert CONFIG.public_domain, "public_domain config is not set"
_COMMUNICATIONSERVICES_CALLABACK_TPL = urljoin(
    str(CONFIG.public_domain),
    "/communicationservices/event/{call_id}/{callback_secret}",
)
logger.info("Using call event URL %s", _COMMUNICATIONSERVICES_CALLABACK_TPL)


@app.route(
    "openapi.json",
    methods=["GET"],
)
@tracer.start_as_current_span("openapi_get")
async def openapi_get(
    req: func.HttpRequest,  # noqa: ARG001
) -> func.HttpResponse:
    """
    Generate the OpenAPI specification for the API.

    No parameters are expected.

    Returns a JSON object with the OpenAPI specification.
    """
    with open(  # noqa: ASYNC230
        encoding="utf-8",
        file=resources_dir("openapi.json"),
    ) as f:
        openapi = json.load(f)
        openapi["info"]["version"] = CONFIG.version
        openapi["servers"] = [
            {
                "description": "Public endpoint",
                "url": str(CONFIG.public_domain),
            }
        ]
        return func.HttpResponse(
            body=json.dumps(openapi),
            mimetype="application/json",
            status_code=HTTPStatus.OK,
        )


@app.route(
    "health/liveness",
    methods=["GET"],
)
@tracer.start_as_current_span("health_liveness_get")
async def health_liveness_get(
    req: func.HttpRequest,  # noqa: ARG001
) -> func.HttpResponse:
    """
    Check if the service is running.

    No parameters are expected.

    Returns a 200 OK if the service is technically running.
    """
    return func.HttpResponse(status_code=HTTPStatus.NO_CONTENT)


@app.route(
    "health/readiness",
    methods=["GET"],
)
@tracer.start_as_current_span("health_readiness_get")
async def health_readiness_get(
    req: func.HttpRequest,  # noqa: ARG001
) -> func.HttpResponse:
    """
    Check if the service is ready to serve requests.

    No parameters are expected. Services tested are: cache, store, search, sms.

    Returns a 200 OK if the service is ready to serve requests. If the service is not ready, it should return a 503 Service Unavailable.
    """
    # Check all components in parallel
    (
        cache_check,
        store_check,
        search_check,
        sms_check,
    ) = await asyncio.gather(
        _cache.areadiness(),
        _db.areadiness(),
        _search.areadiness(),
        _sms.areadiness(),
    )
    readiness = ReadinessModel(
        status=ReadinessEnum.OK,
        checks=[
            ReadinessCheckModel(id="cache", status=cache_check),
            ReadinessCheckModel(id="store", status=store_check),
            ReadinessCheckModel(id="startup", status=ReadinessEnum.OK),
            ReadinessCheckModel(id="search", status=search_check),
            ReadinessCheckModel(id="sms", status=sms_check),
        ],
    )
    # If one of the checks fails, the whole readiness fails
    status_code = HTTPStatus.OK
    for check in readiness.checks:
        if check.status != ReadinessEnum.OK:
            readiness.status = ReadinessEnum.FAIL
            status_code = HTTPStatus.SERVICE_UNAVAILABLE
            break
    return func.HttpResponse(
        body=readiness.model_dump_json(),
        mimetype="application/json",
        status_code=status_code,
    )


@app.route(
    "report",
    methods=["GET"],
    trigger_arg_name="req",
)
@tracer.start_as_current_span("report_get")
async def report_get(req: func.HttpRequest) -> func.HttpResponse:
    """
    List all calls with a web interface.

    Optional URL parameters:
    - phone_number: Filter by phone number

    Returns a list of calls with a web interface.
    """
    try:
        phone_number = (
            PhoneNumber(req.params["phone_number"])
            if "phone_number" in req.params
            else None
        )
    except ValueError as e:
        return _validation_error(e)
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
        applicationinsights_connection_string=getenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING"
        ),
        bot_phone_number=CONFIG.communication_services.phone_number,
        calls=calls or [],
        count=count,
        phone_number=phone_number,
        total=total,
        version=CONFIG.version,
    )
    render = html_minify(render)  # Minify HTML
    return func.HttpResponse(
        body=render,
        mimetype="text/html",
        status_code=HTTPStatus.OK,
    )


@app.route(
    "report/{call_id:guid}",
    methods=["GET"],
    trigger_arg_name="req",
)
@tracer.start_as_current_span("report_single_get")
async def report_single_get(req: func.HttpRequest) -> func.HttpResponse:
    """
    Show a single call with a web interface.

    No parameters are expected.

    Returns a single call with a web interface.
    """
    try:
        call_id = UUID(req.route_params["call_id"])
    except ValueError as e:
        return _validation_error(e)
    call = await _db.call_aget(call_id)
    if not call:
        return _standard_error(
            message=f"Call {call_id} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    template = _jinja.get_template("single.html.jinja")
    render = await template.render_async(
        applicationinsights_connection_string=getenv(
            "APPLICATIONINSIGHTS_CONNECTION_STRING"
        ),
        bot_company=call.initiate.bot_company,
        bot_name=call.initiate.bot_name,
        bot_phone_number=CONFIG.communication_services.phone_number,
        call=call,
        next_actions=[action for action in NextActionEnum],
        version=CONFIG.version,
    )
    render = html_minify(render)  # Minify HTML
    return func.HttpResponse(
        body=render,
        mimetype="text/html",
        status_code=HTTPStatus.OK,
    )


# TODO: Add total (int) and calls (list) as a wrapper for the list of calls
@app.route(
    "call",
    methods=["GET"],
    trigger_arg_name="req",
)
@tracer.start_as_current_span("call_list_get")
async def call_list_get(req: func.HttpRequest) -> func.HttpResponse:
    """
    REST API to list all calls.

    Parameters:
    - phone_number: Filter by phone number

    Returns a list of calls objects `CallGetModel`, for a phone number, in JSON format.
    """
    try:
        phone_number = (
            PhoneNumber(req.params["phone_number"])
            if "phone_number" in req.params
            else None
        )
    except ValueError as e:
        return _validation_error(e)
    count = 100
    calls, _ = await _db.call_asearch_all(phone_number=phone_number, count=count)
    if not calls:
        return _standard_error(
            message=f"Calls {phone_number} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    output = [CallGetModel.model_validate(call) for call in calls or []]
    return func.HttpResponse(
        body=TypeAdapter(list[CallGetModel]).dump_json(output),
        mimetype="application/json",
        status_code=HTTPStatus.OK,
    )


@app.route(
    "call/{phone_number}",
    methods=["GET"],
    trigger_arg_name="req",
)
@tracer.start_as_current_span("call_phone_number_get")
async def call_phone_number_get(req: func.HttpRequest) -> func.HttpResponse:
    """
    REST API to search for calls by phone number.

    Parameters:
    - phone_number: Phone number to search for

    Returns a single call object `CallGetModel`, in JSON format.
    """
    try:
        phone_number = PhoneNumber(req.route_params["phone_number"])
    except ValueError as e:
        return _validation_error(e)
    call = await _db.call_asearch_one(phone_number=phone_number)
    if not call:
        return _standard_error(
            message=f"Call {phone_number} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    return func.HttpResponse(
        body=TypeAdapter(CallGetModel).dump_json(call),
        mimetype="application/json",
        status_code=HTTPStatus.OK,
    )


@app.route(
    "call/{call_id:guid}",
    methods=["GET"],
    trigger_arg_name="req",
)
@tracer.start_as_current_span("call_id_get")
async def call_id_get(req: func.HttpRequest) -> func.HttpResponse:
    """ "
    REST API to get a single call by call ID.

    Parameters:
    - call_id: Call ID to search for

    Returns a single call object `CallGetModel`, in JSON format.
    """
    try:
        call_id = UUID(req.route_params["call_id"])
    except ValueError as e:
        return _validation_error(e)
    call = await _db.call_aget(call_id)
    if not call:
        return _standard_error(
            message=f"Call {call_id} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    return func.HttpResponse(
        body=TypeAdapter(CallGetModel).dump_json(call),
        mimetype="application/json",
        status_code=HTTPStatus.OK,
    )


@app.route(
    "call",
    methods=["POST"],
    trigger_arg_name="req",
)
@tracer.start_as_current_span("call_post")
async def call_post(req: func.HttpRequest) -> func.HttpResponse:
    """
    REST API to initiate a call.

    Required body parameters is a JSON object `CallInitiateModel`.

    Returns a single call object `CallGetModel`, in JSON format.
    """
    try:
        initiate = CallInitiateModel.model_validate_json(req.get_body())
    except ValidationError as e:
        return _validation_error(e)
    url, call = await _communicationservices_event_url(initiate.phone_number, initiate)
    span_attribute(CallAttributes.CALL_ID, str(call.call_id))
    span_attribute(CallAttributes.CALL_PHONE_NUMBER, call.initiate.phone_number)
    automation_client = await _use_automation_client()
    call_connection_properties = await automation_client.create_call(
        callback_url=url,
        cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
        source_caller_id_number=_source_caller,
        # deepcode ignore AttributeLoadOnNone: Phone number is validated with Pydantic
        target_participant=PhoneNumberIdentifier(initiate.phone_number),  # pyright: ignore
    )
    logger.info(
        "Created call with connection id: %s",
        call_connection_properties.call_connection_id,
    )
    return func.HttpResponse(
        body=TypeAdapter(CallGetModel).dump_json(call),
        mimetype="application/json",
        status_code=HTTPStatus.CREATED,
    )


@app.queue_trigger(
    arg_name="call",
    connection="Storage",
    queue_name=CONFIG.communication_services.call_queue_name,
)
@tracer.start_as_current_span("call_event")
async def call_event(
    call: func.QueueMessage,
) -> None:
    """
    Handle incoming call event from Azure Communication Services.

    The event will trigger the workflow to start a new call.

    Queue message is a JSON object `EventGridEvent` with an event type of `AcsIncomingCallEventName`.
    """
    event = EventGridEvent.from_json(call.get_body())
    event_type = event.event_type

    logger.debug("Call event with data %s", event.data)
    if not event_type == SystemEventNames.AcsIncomingCallEventName:
        logger.warning("Event %s not supported", event_type)
        return

    call_context: str = event.data["incomingCallContext"]
    phone_number = PhoneNumber(event.data["from"]["phoneNumber"]["value"])
    url, _call = await _communicationservices_event_url(phone_number)
    span_attribute(CallAttributes.CALL_ID, str(_call.call_id))
    span_attribute(CallAttributes.CALL_PHONE_NUMBER, _call.initiate.phone_number)
    await on_new_call(
        callback_url=url,
        client=await _use_automation_client(),
        incoming_context=call_context,
        phone_number=phone_number,
    )


@app.queue_trigger(
    arg_name="sms",
    connection="Storage",
    queue_name=CONFIG.communication_services.sms_queue_name,
)
@app.queue_output(
    arg_name="trainings",
    connection="Storage",
    queue_name=CONFIG.communication_services.trainings_queue_name,
)
@app.queue_output(
    arg_name="post",
    connection="Storage",
    queue_name=CONFIG.communication_services.post_queue_name,
)
@tracer.start_as_current_span("sms_event")
async def sms_event(
    post: func.Out[str],
    sms: func.QueueMessage,
    trainings: func.Out[str],
) -> None:
    """
    Handle incoming SMS event from Azure Communication Services.

    The event will trigger the workflow to handle a new SMS message.

    Returns None. Can trigger additional events to `trainings` and `post` queues.
    """
    event = EventGridEvent.from_json(sms.get_body())
    event_type = event.event_type

    logger.debug("SMS event with data %s", event.data)
    if not event_type == SystemEventNames.AcsSmsReceivedEventName:
        logger.warning("Event %s not supported", event_type)
        return

    message: str = event.data["message"]
    phone_number: str = event.data["from"]
    span_attribute(CallAttributes.CALL_PHONE_NUMBER, phone_number)
    call = await _db.call_asearch_one(phone_number)
    if not call:
        logger.warning("Call for phone number %s not found", phone_number)
        return
    span_attribute(CallAttributes.CALL_ID, str(call.call_id))

    async def _post_callback(_call: CallStateModel) -> None:
        _trigger_post_event(call=_call, post=post)

    async def _trainings_callback(_call: CallStateModel) -> None:
        _trigger_trainings_event(call=_call, trainings=trainings)

    await on_sms_received(
        call=call,
        client=await _use_automation_client(),
        message=message,
        post_callback=_post_callback,
        trainings_callback=_trainings_callback,
    )


@app.route(
    "communicationservices/event/{call_id:guid}/{secret:length(16)}",
    methods=["POST"],
    trigger_arg_name="req",
)
@app.queue_output(
    arg_name="trainings",
    connection="Storage",
    queue_name=CONFIG.communication_services.trainings_queue_name,
)
@app.queue_output(
    arg_name="post",
    connection="Storage",
    queue_name=CONFIG.communication_services.post_queue_name,
)
@tracer.start_as_current_span("communicationservices_event_post")
async def communicationservices_event_post(
    post: func.Out[str],
    req: func.HttpRequest,
    trainings: func.Out[str],
) -> func.HttpResponse:
    """
    Handle direct events from Azure Communication Services for a running call.

    No parameters are expected. The body is a list of JSON objects `CloudEvent`.

    Returns a 204 No Content if the events are properly fomatted. A 401 Unauthorized if the JWT token is invalid. Otherwise, returns a 400 Bad Request.
    """
    # Validate JWT token
    service_jwt: str | None = req.headers.get("Authorization")
    if not service_jwt:
        return _standard_error(
            message="Authorization header missing",
            status_code=HTTPStatus.UNAUTHORIZED,
        )
    service_jwt = str(service_jwt).replace("Bearer ", "")
    try:
        jwt.decode(
            algorithms=["RS256"],
            audience=CONFIG.communication_services.resource_id,
            issuer="https://acscallautomation.communication.azure.com",
            jwt=service_jwt,
            leeway=timedelta(
                minutes=5
            ),  # Recommended practice by Azure to mitigate clock skew
            key=_communication_services_jwks_client.get_signing_key_from_jwt(
                service_jwt
            ).key,
        )
    except jwt.PyJWTError:
        logger.warning("Invalid JWT token", exc_info=True)
        return _standard_error(
            message="Invalid JWT token",
            status_code=HTTPStatus.UNAUTHORIZED,
        )

    # Validate request
    try:
        call_id = UUID(req.route_params["call_id"])
        secret: str = req.route_params["secret"]
    except ValueError as e:
        return _validation_error(e)
    try:
        events = req.get_json()
    except ValueError:
        return _validation_error(Exception("Invalid JSON format"))
    if not events or not isinstance(events, list):
        return _validation_error(Exception("Events must be a list"))

    # Process events in parallel
    await asyncio.gather(
        *[
            _communicationservices_event_worker(
                call_id=call_id,
                event_dict=event,
                post=post,
                secret=secret,
                trainings=trainings,
            )
            for event in events
        ]
    )

    # Return default response
    return func.HttpResponse(status_code=HTTPStatus.NO_CONTENT)


# TODO: Refacto, too long (and remove PLR0912/PLR0915 ignore)
async def _communicationservices_event_worker(  # noqa: PLR0912, PLR0915
    call_id: UUID,
    event_dict: dict,
    post: func.Out[str],
    secret: str,
    trainings: func.Out[str],
) -> None:
    """
    Worker to handle a single event from Azure Communication Services.

    The event will trigger the workflow to handle a new event for a running call:
    - Call connected
    - Call disconnected
    - Call transfer accepted
    - Call transfer failed
    - Play completed
    - Play failed
    - Recognize completed
    - Recognize failed

    Returns None. Can trigger additional events to `trainings` and `post` queues.
    """
    span_attribute(CallAttributes.CALL_ID, str(call_id))
    call = await _db.call_aget(call_id)
    if not call:
        logger.warning("Call %s not found", call_id)
        return
    if call.callback_secret != secret:
        logger.warning("Secret for call %s does not match", call_id)
        return

    span_attribute(CallAttributes.CALL_PHONE_NUMBER, call.initiate.phone_number)
    # Event parsing
    event = CloudEvent.from_dict(event_dict)
    assert isinstance(event.data, dict)
    # Store connection ID
    connection_id = event.data["callConnectionId"]
    call.voice_id = connection_id
    # Extract context
    event_type = event.type
    # Extract event context
    operation_context = event.data.get("operationContext", None)
    operation_contexts = _str_to_contexts(operation_context)
    # Client SDK
    automation_client = await _use_automation_client()

    logger.debug("Call event received %s for call %s", event_type, call)
    logger.debug(event.data)

    async def _post_callback(_call: CallStateModel) -> None:
        _trigger_post_event(call=_call, post=post)

    async def _trainings_callback(_call: CallStateModel) -> None:
        _trigger_trainings_event(call=_call, trainings=trainings)

    if event_type == "Microsoft.Communication.CallConnected":  # Call answered
        await on_call_connected(
            call=call,
            client=automation_client,
        )

    elif event_type == "Microsoft.Communication.CallDisconnected":  # Call hung up
        await on_call_disconnected(
            call=call,
            client=automation_client,
            post_callback=_post_callback,
        )

    elif (
        event_type == "Microsoft.Communication.RecognizeCompleted"
    ):  # Speech recognized
        recognition_result: str = event.data["recognitionType"]

        if recognition_result == "speech":  # Handle voice
            speech_text: str | None = event.data["speechResult"]["speech"]
            if speech_text:
                await on_speech_recognized(
                    call=call,
                    client=automation_client,
                    post_callback=_post_callback,
                    text=speech_text,
                    trainings_callback=_trainings_callback,
                )

        elif recognition_result == "choices":  # Handle IVR
            label_detected: str = event.data["choiceResult"]["label"]
            await on_ivr_recognized(
                call=call,
                client=automation_client,
                label=label_detected,
                post_callback=_post_callback,
                trainings_callback=_trainings_callback,
            )

    elif (
        event_type == "Microsoft.Communication.RecognizeFailed"
    ):  # Speech recognition failed
        result_information = event.data["resultInformation"]
        error_code: int = result_information["subCode"]
        error_message: str = result_information["message"]
        logger.debug(
            "Speech recognition failed with error code %s: %s",
            error_code,
            error_message,
        )
        # Error codes:
        # 8510 = Action failed, initial silence timeout reached
        # 8532 = Action failed, inter-digit silence timeout reached
        # See: https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/communication-services/how-tos/call-automation/recognize-action.md#event-codes
        if error_code in (8510, 8532):  # Timeout retry
            await on_recognize_timeout_error(
                call=call,
                client=automation_client,
                contexts=operation_contexts,
            )
        else:  # Unknown error
            await on_recognize_unknown_error(
                call=call,
                client=automation_client,
                error_code=error_code,
            )

    elif event_type == "Microsoft.Communication.PlayCompleted":  # Media played
        await on_play_completed(
            call=call,
            client=automation_client,
            contexts=operation_contexts,
            post_callback=_post_callback,
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
            client=automation_client,
            error_code=sub_code,
        )

    await _db.call_aset(
        call
    )  # TODO: Do not persist on every event, this is simpler but not efficient


@app.queue_trigger(
    arg_name="trainings",
    connection="Storage",
    queue_name=CONFIG.communication_services.trainings_queue_name,
)
@tracer.start_as_current_span("trainings_event")
async def trainings_event(
    trainings: func.QueueMessage,
) -> None:
    """
    Handle trainings event from the queue.

    Queue message is a JSON object `CallStateModel`. The event will load asynchroniously the trainings for a call.

    Returns None.
    """
    call = CallStateModel.model_validate_json(trainings.get_body())
    logger.debug("Trainings event received for call %s", call)
    span_attribute(CallAttributes.CALL_ID, str(call.call_id))
    span_attribute(CallAttributes.CALL_PHONE_NUMBER, call.initiate.phone_number)
    await call.trainings(cache_only=False)  # Get trainings by advance to populate cache


@app.queue_trigger(
    arg_name="post",
    connection="Storage",
    queue_name=CONFIG.communication_services.post_queue_name,
)
@tracer.start_as_current_span("post_event")
async def post_event(
    post: func.QueueMessage,
) -> None:
    """
    Handle post-call intelligence event from the queue.

    Queue message is a JSON object `CallStateModel`. The event will load asynchroniously the `on_end_call` workflow.
    """
    call = CallStateModel.model_validate_json(post.get_body())
    logger.debug("Post event received for call %s", call)
    span_attribute(CallAttributes.CALL_ID, str(call.call_id))
    span_attribute(CallAttributes.CALL_PHONE_NUMBER, call.initiate.phone_number)
    await on_end_call(call)


def _trigger_trainings_event(
    call: CallStateModel,
    trainings: func.Out[str],
) -> None:
    """
    Shortcut to add trainings to the queue.
    """
    trainings.set(call.model_dump_json(exclude_none=True))


def _trigger_post_event(
    call: CallStateModel,
    post: func.Out[str],
) -> None:
    """
    Shortcut to add post-call intelligence to the queue.
    """
    post.set(call.model_dump_json(exclude_none=True))


async def _communicationservices_event_url(
    phone_number: PhoneNumber, initiate: CallInitiateModel | None = None
) -> tuple[str, CallStateModel]:
    """
    Generate the callback URL for a call.

    If the caller has already called, use the same call ID, to keep the conversation history. Otherwise, create a new call ID.
    """
    call = await _db.call_asearch_one(phone_number)
    if not call or (
        initiate and call.initiate != initiate
    ):  # Create new call if initiate is different
        call = CallStateModel(
            initiate=initiate
            or CallInitiateModel(
                **CONFIG.conversation.initiate.model_dump(),
                phone_number=phone_number,
            )
        )
        await _db.call_aset(call)  # Create for the first time
    url = _COMMUNICATIONSERVICES_CALLABACK_TPL.format(
        callback_secret=call.callback_secret,
        call_id=str(call.call_id),
    )
    return url, call


# TODO: Secure this endpoint with a secret, either in the Authorization header or in the URL
@app.route(
    "twilio/sms",
    methods=["POST"],
    trigger_arg_name="req",
)
@app.queue_output(
    arg_name="trainings",
    connection="Storage",
    queue_name=CONFIG.communication_services.trainings_queue_name,
)
@app.queue_output(
    arg_name="post",
    connection="Storage",
    queue_name=CONFIG.communication_services.post_queue_name,
)
@tracer.start_as_current_span("twilio_sms_post")
async def twilio_sms_post(
    post: func.Out[str],
    req: func.HttpRequest,
    trainings: func.Out[str],
) -> func.HttpResponse:
    """
    Handle incoming SMS event from Twilio.

    The event will trigger the workflow to handle a new SMS message.

    Returns a 200 OK if the SMS is properly formatted. Otherwise, returns a 400 Bad Request.
    """
    if not req.form:
        return _validation_error(Exception("No form data"))
    try:
        phone_number = PhoneNumber(req.form["From"])
        message: str = req.form["Body"]
    except ValueError as e:
        return _validation_error(e)

    span_attribute(CallAttributes.CALL_PHONE_NUMBER, phone_number)
    call = await _db.call_asearch_one(phone_number)

    if not call:
        logger.warning("Call for phone number %s not found", phone_number)

    else:
        span_attribute(CallAttributes.CALL_ID, str(call.call_id))

        async def _post_callback(_call: CallStateModel) -> None:
            _trigger_post_event(call=_call, post=post)

        async def _trainings_callback(_call: CallStateModel) -> None:
            _trigger_trainings_event(call=_call, trainings=trainings)

        event_status = await on_sms_received(
            call=call,
            client=await _use_automation_client(),
            message=message,
            post_callback=_post_callback,
            trainings_callback=_trainings_callback,
        )
        if not event_status:
            return _standard_error(
                message="SMS event failed",
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    return func.HttpResponse(
        body=str(MessagingResponse()),  # Twilio expects an empty response everytime
        mimetype="application/xml",
        status_code=HTTPStatus.OK,
    )


def _str_to_contexts(value: str | None) -> set[CallContextEnum] | None:
    """
    Convert a string to a set of contexts.

    The string is a JSON array of strings.

    Returns a set of `CallContextEnum` or None.
    """
    if not value:
        return None
    try:
        contexts = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    res = set()
    for context in contexts:
        try:
            res.add(CallContextEnum(context))
        except ValueError:
            logger.warning("Unknown context %s, skipping", context)
    return res or None


def _validation_error(
    e: Exception,
) -> func.HttpResponse:
    """
    Generate a standard validation error response.

    Response body is a JSON object with the following structure:

    ```
    {"error": {"message": "Validation error", "details": ["Error message"]}}
    ```

    Returns a 400 Bad Request with a JSON body.
    """
    messages = []
    if isinstance(e, ValidationError):
        messages = [
            str(x) for x in e.errors()
        ]  # Pydantic returns well formatted errors, use them
    elif isinstance(e, ValueError):
        messages = [str(e)]  # TODO: Could it expose sensitive information?
    return _standard_error(
        details=messages,
        message="Validation error",
        status_code=HTTPStatus.BAD_REQUEST,
    )


def _standard_error(
    message: str,
    details: list[str] | None = None,
    status_code: HTTPStatus = HTTPStatus.BAD_REQUEST,
) -> func.HttpResponse:
    """
    Generate a standard error response.

    Response body is a JSON object with the following structure:

    ```
    {"error": {"message": "Error message", "details": ["Error details"]}}
    ```

    Returns a JOSN with a JSON body and the specified status code.
    """
    res_json = {
        "error": {
            "message": message,
            "details": details or [],
        }
    }
    return func.HttpResponse(
        body=json.dumps(res_json),
        mimetype="application/json",
        status_code=status_code,
    )


async def _use_automation_client() -> CallAutomationClient:
    """
    Get the call automation client for Azure Communication Services.

    Object is cached for performance.

    Returns a `CallAutomationClient` instance.
    """
    global _automation_client  # noqa: PLW0603
    if not isinstance(_automation_client, CallAutomationClient):
        _automation_client = CallAutomationClient(
            # Deployment
            endpoint=CONFIG.communication_services.endpoint,
            # Performance
            transport=await azure_transport(),
            # Authentication
            credential=AzureKeyCredential(
                CONFIG.communication_services.access_key.get_secret_value()
            ),  # Cannot place calls with RBAC, need to use access key (see: https://learn.microsoft.com/en-us/azure/communication-services/concepts/authentication#authentication-options)
        )
    return _automation_client
