import asyncio
import json
import time
from base64 import b64decode, b64encode
from contextlib import asynccontextmanager
from datetime import timedelta
from http import HTTPStatus
from os import getenv
from typing import Annotated, Any
from urllib.parse import quote_plus, urljoin
from uuid import UUID

import jwt
import mistune
from azure.communication.callautomation import (
    MediaStreamingAudioChannelType,
    MediaStreamingContentType,
    MediaStreamingOptions,
    MediaStreamingTransportType,
    PhoneNumberIdentifier,
)
from azure.communication.callautomation.aio import CallAutomationClient
from azure.core.credentials import AzureKeyCredential
from azure.core.messaging import CloudEvent
from azure.eventgrid import EventGridEvent, SystemEventNames
from fastapi import (
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError, ValidationException
from fastapi.responses import HTMLResponse, JSONResponse
from htmlmin.minify import html_minify
from jinja2 import Environment, FileSystemLoader
from pydantic import Field, TypeAdapter, ValidationError
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from twilio.twiml.messaging_response import MessagingResponse

from app.helpers.cache import get_scheduler, lru_acache
from app.helpers.call_events import (
    on_audio_connected,
    on_automation_play_completed,
    on_automation_recognize_error,
    on_call_connected,
    on_call_disconnected,
    on_end_call,
    on_ivr_recognized,
    on_new_call,
    on_play_error,
    on_play_started,
    on_sms_received,
    on_transfer_error,
)
from app.helpers.call_utils import ContextEnum as CallContextEnum
from app.helpers.config import CONFIG
from app.helpers.http import aiohttp_session, azure_transport
from app.helpers.logging import logger
from app.helpers.monitoring import (
    SpanAttributeEnum,
    call_frames_in_latency,
    call_frames_out_latency,
    gauge_set,
    start_as_current_span,
    suppress,
)
from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.helpers.resources import resources_dir
from app.models.call import CallGetModel, CallInitiateModel, CallStateModel
from app.models.error import ErrorInnerModel, ErrorModel
from app.models.next import ActionEnum as NextActionEnum
from app.models.readiness import ReadinessCheckModel, ReadinessEnum, ReadinessModel
from app.persistence.azure_queue_storage import (
    Message as AzureQueueStorageMessage,
)

# First log
logger.info(
    "call-center-ai v%s",
    CONFIG.version,
)

# Jinja configuration
_jinja = Environment(
    auto_reload=False,  # Disable auto-reload for performance
    autoescape=True,
    enable_async=True,
    loader=FileSystemLoader(resources_dir("public_website")),
    optimized=False,  # Outsource optimization to html_minify
)
# Jinja custom functions
_jinja.filters["quote_plus"] = lambda x: quote_plus(str(x)) if x else ""
_jinja.filters["markdown"] = lambda x: (
    mistune.create_markdown(plugins=["abbr", "speedup", "url"])(x) if x else ""
)  # pyright: ignore

# Azure Communication Services
_source_caller = PhoneNumberIdentifier(CONFIG.communication_services.phone_number)
logger.info("Using phone number %s", CONFIG.communication_services.phone_number)
_communication_services_jwks_client = jwt.PyJWKClient(
    cache_keys=True,
    uri="https://acscallautomation.communication.azure.com/calling/keys",
)

# Persistences
_cache = CONFIG.cache.instance
_call_queue = CONFIG.queue.call
_db = CONFIG.database.instance
_post_queue = CONFIG.queue.post
_search = CONFIG.ai_search.instance
_sms = CONFIG.sms.instance
_sms_queue = CONFIG.queue.sms
_training_queue = CONFIG.queue.training

# Communication Services callback
assert CONFIG.public_domain, "public_domain config is not set"
_COMMUNICATIONSERVICES_WSS_TPL = urljoin(
    str(CONFIG.public_domain).replace("https://", "wss://"),
    "/communicationservices/wss/{call_id}/{callback_secret}",
)
logger.info("Using WebSocket URL %s", _COMMUNICATIONSERVICES_WSS_TPL)
_COMMUNICATIONSERVICES_CALLABACK_TPL = urljoin(
    str(CONFIG.public_domain),
    "/communicationservices/callback/{call_id}/{callback_secret}",
)
logger.info("Using callback URL %s", _COMMUNICATIONSERVICES_CALLABACK_TPL)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    queue_tasks = None

    try:
        queue_tasks = asyncio.gather(
            _call_queue.trigger(
                arg="call",
                func=call_event,
            ),
            _post_queue.trigger(
                arg="post",
                func=post_event,
            ),
            _sms_queue.trigger(
                arg="sms",
                func=sms_event,
            ),
            _training_queue.trigger(
                arg="training",
                func=training_event,
            ),
        )
        yield

    # Cancel tasks
    finally:
        if queue_tasks:
            queue_tasks.cancel()

    # Close HTTP session
    await (await aiohttp_session()).close()


# FastAPI
api = FastAPI(
    contact={
        "url": "https://github.com/microsoft/call-center-ai",
    },
    description="Send a phone call from AI agent, in an API call. Or, directly call the bot from the configured phone number!",
    license_info={
        "name": "Apache-2.0",
        "url": "https://github.com/microsoft/call-center-ai/blob/master/LICENSE",
    },
    lifespan=lifespan,
    title="call-center-ai",
    version=CONFIG.version,
)


@api.get("/health/liveness")
@start_as_current_span("health_liveness_get")
async def health_liveness_get() -> None:
    """
    Check if the service is running.

    No parameters are expected.

    Returns a 200 OK if the service is technically running.
    """
    return


@api.get(
    "/health/readiness",
    status_code=HTTPStatus.OK,
)
@start_as_current_span("health_readiness_get")
async def health_readiness_get() -> JSONResponse:
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
        _cache.readiness(),
        _db.readiness(),
        _search.readiness(),
        _sms.readiness(),
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
    return JSONResponse(
        content=readiness.model_dump(mode="json"),
        status_code=status_code,
    )


@api.get(
    "/report",
    response_class=HTMLResponse,
)
@start_as_current_span("report_get")
async def report_get(phone_number: str | None = None) -> HTMLResponse:
    """
    List all calls with a web interface.

    Optional URL parameters:
    - phone_number: Filter by phone number

    Returns a list of calls with a web interface.
    """
    phone_number = PhoneNumber(phone_number) if phone_number else None
    count = 100
    calls, total = (
        await _db.call_search_all(count=count, phone_number=phone_number) or []
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
    return HTMLResponse(
        content=render,
        status_code=HTTPStatus.OK,
    )


@api.get(
    "/report/{call_id}",
    response_class=HTMLResponse,
)
@start_as_current_span("report_single_get")
async def report_single_get(call_id: UUID) -> HTMLResponse:
    """
    Show a single call with a web interface.

    No parameters are expected.

    Returns a single call with a web interface.
    """
    call = await _db.call_get(call_id)
    if not call:
        return HTMLResponse(
            content=f"Call {call_id} not found",
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
    return HTMLResponse(content=render, status_code=HTTPStatus.OK)


@api.get("/call")
@start_as_current_span("call_list_get")
async def call_list_get(
    phone_number: str | None = None,
) -> list[CallGetModel]:
    """
    REST API to list all calls.

    Parameters:
    - phone_number: Filter by phone number

    Returns a list of calls objects `CallGetModel`, for a phone number, in JSON format.
    """
    phone_number = PhoneNumber(phone_number) if phone_number else None
    count = 100
    calls, _ = await _db.call_search_all(phone_number=phone_number, count=count)
    if not calls:
        raise HTTPException(
            detail=f"Call {phone_number} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    output = [CallGetModel.model_validate(call) for call in calls or []]
    return TypeAdapter(list[CallGetModel]).dump_python(output)


@api.get("/call/{call_id_or_phone_number}")
@start_as_current_span("call_get")
async def call_get(call_id_or_phone_number: str) -> CallGetModel:
    """
    REST API to search for calls by call ID or phone number.

    Parameters:
    - call_id_or_phone_number: Call ID or phone number to search for

    Returns a single call object `CallGetModel`, in JSON format.
    """
    # First, try to get by call ID
    with suppress(ValueError):
        call_id = UUID(call_id_or_phone_number)
        call = await _db.call_get(call_id)
        if call:
            return TypeAdapter(CallGetModel).dump_python(call)

    # Second, try to get by phone number
    phone_number = PhoneNumber(call_id_or_phone_number)
    call = await _db.call_search_one(
        callback_timeout=False,
        phone_number=phone_number,
    )
    if not call:
        raise HTTPException(
            detail=f"Call {call_id_or_phone_number} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    return TypeAdapter(CallGetModel).dump_python(call)


@api.post(
    "/call",
    status_code=HTTPStatus.CREATED,
)
@start_as_current_span("call_post")
async def call_post(request: Request) -> CallGetModel:
    """
    REST API to initiate a call.

    Required body parameters is a JSON object `CallInitiateModel`.

    Returns a single call object `CallGetModel`, in JSON format.
    """
    try:
        body = await request.json()
        initiate = CallInitiateModel.model_validate(body)
    except ValidationError as e:
        raise RequestValidationError([str(e)]) from e

    # Get URLs
    callback_url, wss_url, call = await _communicationservices_urls(
        initiate.phone_number, initiate
    )

    # Enrich span
    SpanAttributeEnum.CALL_ID.attribute(str(call.call_id))
    SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(call.initiate.phone_number)

    # Init SDK
    automation_client = await _use_automation_client()
    streaming_options = MediaStreamingOptions(
        audio_channel_type=MediaStreamingAudioChannelType.UNMIXED,
        content_type=MediaStreamingContentType.AUDIO,
        start_media_streaming=False,
        transport_type=MediaStreamingTransportType.WEBSOCKET,
        transport_url=wss_url,
    )
    call_connection_properties = await automation_client.create_call(
        callback_url=callback_url,
        cognitive_services_endpoint=CONFIG.cognitive_service.endpoint,
        media_streaming=streaming_options,
        source_caller_id_number=_source_caller,
        target_participant=PhoneNumberIdentifier(initiate.phone_number),  # pyright: ignore
    )

    logger.info(
        "Created call with connection id: %s",
        call_connection_properties.call_connection_id,
    )

    return TypeAdapter(CallGetModel).dump_python(call)


@start_as_current_span("call_event")
async def call_event(
    call: AzureQueueStorageMessage,
) -> None:
    """
    Handle incoming call event from Azure Communication Services.

    The event will trigger the workflow to start a new call.

    Queue message is a JSON object `EventGridEvent` with an event type of `AcsIncomingCallEventName`.
    """
    # Parse event
    event = EventGridEvent.from_json(call.content)
    event_type = event.event_type
    if not event_type == SystemEventNames.AcsIncomingCallEventName:
        logger.warning("Event %s not supported", event_type)
        # logger.debug("Event data %s", event.data)
        return

    # Parse phone number
    call_context: str = event.data["incomingCallContext"]
    phone_number = PhoneNumber(event.data["from"]["phoneNumber"]["value"])

    # Get URLs
    callback_url, wss_url, _call = await _communicationservices_urls(phone_number)

    # Enrich span
    SpanAttributeEnum.CALL_ID.attribute(str(_call.call_id))
    SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(_call.initiate.phone_number)

    # Execute business logic
    await on_new_call(
        callback_url=callback_url,
        client=await _use_automation_client(),
        incoming_context=call_context,
        phone_number=phone_number,
        wss_url=wss_url,
    )


@start_as_current_span("sms_event")
async def sms_event(
    sms: AzureQueueStorageMessage,
) -> None:
    """
    Handle incoming SMS event from Azure Communication Services.

    The event will trigger the workflow to handle a new SMS message.

    Returns None. Can trigger additional events to `training` and `post` queues.
    """
    # Parse event
    event = EventGridEvent.from_json(sms.content)
    event_type = event.event_type
    logger.debug("SMS event with data %s", event.data)

    # Skip non-SMS events
    if not event_type == SystemEventNames.AcsSmsReceivedEventName:
        logger.warning("Event %s not supported", event_type)
        return

    message: str = event.data["message"]
    phone_number: str = event.data["from"]

    # Enrich span
    SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(phone_number)

    async with get_scheduler() as scheduler:
        # Get call
        call = await _db.call_search_one(
            callback_timeout=False,
            phone_number=phone_number,
        )
        if not call:
            logger.warning("Call for phone number %s not found", phone_number)
            return

    # Enrich span
    SpanAttributeEnum.CALL_ID.attribute(str(call.call_id))

    async with get_scheduler() as scheduler:
        # Execute business logic
        await on_sms_received(
            call=call,
            message=message,
            scheduler=scheduler,
        )


async def _communicationservices_validate_jwt(
    headers: Headers,
) -> None:
    # Validate JWT token
    service_jwt: str | None = headers.get("Authorization")
    if not service_jwt:
        raise HTTPException(
            detail="Authorization header missing",
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
    except jwt.PyJWTError as e:
        raise HTTPException(
            detail="Invalid JWT token",
            status_code=HTTPStatus.UNAUTHORIZED,
        ) from e


async def _communicationservices_validate_call_id(
    call_id: UUID,
    secret: str,
) -> CallStateModel:
    # Enrich span
    SpanAttributeEnum.CALL_ID.attribute(str(call_id))

    # Validate call
    call = await _db.call_get(call_id)
    if not call:
        raise HTTPException(
            detail=f"Call {call_id} not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    # Validate secret
    if call.callback_secret != secret:
        raise HTTPException(
            detail="Secret does not match",
            status_code=HTTPStatus.UNAUTHORIZED,
        )

    # Enrich span
    SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(call.initiate.phone_number)

    return call


@api.websocket("/communicationservices/wss/{call_id}/{secret}")
@start_as_current_span("communicationservices_event_post")
async def communicationservices_wss_post(
    call_id: UUID,
    secret: Annotated[str, Field(min_length=16, max_length=16)],
    websocket: WebSocket,
) -> None:
    # Validate connection
    # TODO: Uncomment when JWT validation is fixed
    # await _communicationservices_validate_jwt(websocket.headers)
    call = await _communicationservices_validate_call_id(call_id, secret)

    # Accept connection
    await websocket.accept()
    logger.info("WebSocket connection established")

    # Client SDK
    automation_client = await _use_automation_client()

    # Queues
    audio_in: asyncio.Queue[bytes] = asyncio.Queue()
    audio_out: asyncio.Queue[bytes | bool] = asyncio.Queue()

    async def _consume_audio() -> None:
        """
        Consume audio data from the WebSocket.
        """
        logger.debug("Audio data consumer started")

        # Loop until the WebSocket is disconnected
        with suppress(WebSocketDisconnect):
            start: float | None = None
            async for event in websocket.iter_json():
                # TODO: Handle configuration event (audio format, sample rate, etc.)
                # Skip non-audio events
                if "kind" not in event or event["kind"] != "AudioData":
                    continue

                # Filter out silent audio
                audio_data: dict[str, Any] = event.get("audioData", {})
                audio_base64: str | None = audio_data.get("data", None)
                audio_silent: bool | None = audio_data.get("silent", True)
                if audio_silent or not audio_base64:
                    continue

                # Queue audio
                await audio_in.put(b64decode(audio_base64))

                # Report the frames in latency and reset the timer
                if start:
                    gauge_set(
                        metric=call_frames_in_latency,
                        value=time.monotonic() - start,
                    )
                start = time.monotonic()

        logger.debug("Audio data consumer stopped")

    async def _send_audio() -> None:
        """
        Send audio data to the WebSocket
        """
        logger.debug("Audio data sender started")

        # Loop until the WebSocket is disconnected
        with suppress(WebSocketDisconnect):
            start: float | None = None
            while True:
                # Get audio
                audio_data = await audio_out.get()
                audio_out.task_done()

                # Send audio
                if isinstance(audio_data, bytes):
                    await websocket.send_json(
                        {
                            "kind": "AudioData",
                            "audioData": {
                                "data": b64encode(audio_data).decode("utf-8"),
                            },
                        }
                    )

                # Stop audio
                elif audio_data is False:
                    logger.debug("Stop audio event received, stopping audio")
                    await websocket.send_json(
                        {
                            "kind": "StopAudio",
                            "stopAudio": {},
                        }
                    )

                # Report the frames out latency and reset the timer
                if start:
                    gauge_set(
                        metric=call_frames_out_latency,
                        value=time.monotonic() - start,
                    )
                start = time.monotonic()

        logger.debug("Audio data sender stopped")

    async with get_scheduler() as scheduler:
        await asyncio.gather(
            # Consume audio from the WebSocket
            _consume_audio(),
            # Send audio to the WebSocket
            _send_audio(),
            # Process audio
            # TODO: Dynamically set the audio format
            on_audio_connected(
                audio_in=audio_in,
                audio_out=audio_out,
                audio_sample_rate=16000,
                call=call,
                client=automation_client,
                post_callback=_trigger_post_event,
                scheduler=scheduler,
                training_callback=_trigger_training_event,
            ),
        )


@api.post("/communicationservices/callback/{call_id}/{secret}")
@start_as_current_span("communicationservices_callback_post")
async def communicationservices_callback_post(
    call_id: UUID,
    request: Request,
    secret: Annotated[str, Field(min_length=16, max_length=16)],
) -> None:
    """
    Handle direct events from Azure Communication Services for a running call.

    No parameters are expected. The body is a list of JSON objects `CloudEvent`.

    Returns a 204 No Content if the events are properly formatted. A 401 Unauthorized if the JWT token is invalid. Otherwise, returns a 400 Bad Request.
    """

    # Validate connection
    await _communicationservices_validate_jwt(request.headers)

    # Validate request
    events = await request.json()
    if not events or not isinstance(events, list):
        raise RequestValidationError(["Events must be a list"])

    # Process events in parallel
    await asyncio.gather(
        *[
            _communicationservices_event_worker(
                call_id=call_id,
                event_dict=event,
                secret=secret,
            )
            for event in events
        ]
    )


# TODO: Refacto, too long (and remove PLR0912/PLR0915 ignore)
async def _communicationservices_event_worker(
    call_id: UUID,
    event_dict: dict,
    secret: str,
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

    Returns None. Can trigger additional events to `training` and `post` queues.
    """

    # Validate connection
    call = await _communicationservices_validate_call_id(call_id, secret)

    # Event parsing
    event = CloudEvent.from_dict(event_dict)
    assert isinstance(event.data, dict)

    async with get_scheduler() as scheduler:
        # Store connection ID
        connection_id = event.data["callConnectionId"]
        async with _db.call_transac(
            call=call,
            scheduler=scheduler,
        ):
            call.voice_id = connection_id

        # Extract context
        event_type = event.type

        # Extract event context
        operation_context = event.data.get("operationContext", None)
        operation_contexts = _str_to_contexts(operation_context)

        # Client SDK
        automation_client = await _use_automation_client()

        # Log
        logger.debug("Call event received %s", event_type)

        match event_type:
            # Call answered
            case "Microsoft.Communication.CallConnected":
                server_call_id = event.data["serverCallId"]
                await on_call_connected(
                    call=call,
                    client=automation_client,
                    scheduler=scheduler,
                    server_call_id=server_call_id,
                )

            # Call hung up
            case "Microsoft.Communication.CallDisconnected":
                await on_call_disconnected(
                    call=call,
                    client=automation_client,
                    post_callback=_trigger_post_event,
                    scheduler=scheduler,
                )

            # Speech/IVR recognized
            case "Microsoft.Communication.RecognizeCompleted":
                recognition_result: str = event.data["recognitionType"]
                # Handle IVR
                if recognition_result == "choices":
                    label_detected: str = event.data["choiceResult"]["label"]
                    await on_ivr_recognized(
                        call=call,
                        client=automation_client,
                        label=label_detected,
                        scheduler=scheduler,
                    )

            # Speech/IVR failed
            case "Microsoft.Communication.RecognizeFailed":
                result_information = event.data["resultInformation"]
                error_code: int = result_information["subCode"]
                error_message: str = result_information["message"]
                logger.debug(
                    "Speech recognition failed with error code %s: %s",
                    error_code,
                    error_message,
                )
                await on_automation_recognize_error(
                    call=call,
                    client=automation_client,
                    contexts=operation_contexts,
                    post_callback=_trigger_post_event,
                    scheduler=scheduler,
                )

            # Media started
            case "Microsoft.Communication.PlayStarted":
                await on_play_started(
                    call=call,
                    scheduler=scheduler,
                )

            # Media played
            case "Microsoft.Communication.PlayCompleted":
                await on_automation_play_completed(
                    call=call,
                    client=automation_client,
                    contexts=operation_contexts,
                    post_callback=_trigger_post_event,
                    scheduler=scheduler,
                )

            # Media play failed
            case "Microsoft.Communication.PlayFailed":
                result_information = event.data["resultInformation"]
                error_code: int = result_information["subCode"]
                await on_play_error(error_code)

            # Call transfer failed
            case "Microsoft.Communication.CallTransferFailed":
                result_information = event.data["resultInformation"]
                sub_code: int = result_information["subCode"]
                await on_transfer_error(
                    call=call,
                    client=automation_client,
                    error_code=sub_code,
                    post_callback=_trigger_post_event,
                    scheduler=scheduler,
                )

            case _:
                logger.warning("Event %s not supported", event_type)
                # logger.debug("Event data %s", event.data)


@start_as_current_span("training_event")
async def training_event(
    training: AzureQueueStorageMessage,
) -> None:
    """
    Handle training event from the queue.

    Queue message is a JSON object `CallStateModel`. The event will load asynchroniously the training for a call.

    Returns None.
    """
    # Validate call
    call = CallStateModel.model_validate_json(training.content)

    # Enrich span
    SpanAttributeEnum.CALL_ID.attribute(str(call.call_id))
    SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(call.initiate.phone_number)

    logger.debug("Training event received")

    # Load trainings
    await call.trainings(cache_only=False)  # Get trainings by advance to populate cache


@start_as_current_span("post_event")
async def post_event(
    post: AzureQueueStorageMessage,
) -> None:
    """
    Handle post-call intelligence event from the queue.

    Queue message is the UUID of a call. The event will load asynchroniously the `on_end_call` workflow.
    """
    async with get_scheduler() as scheduler:
        # Validate call
        call = await _db.call_get(UUID(post.content))
        if not call:
            logger.warning("Call %s not found", post.content)
            return

        # Enrich span
        SpanAttributeEnum.CALL_ID.attribute(str(call.call_id))
        SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(call.initiate.phone_number)

        # Execute business logic
        logger.debug("Post event received")
        await on_end_call(
            call=call,
            scheduler=scheduler,
        )


async def _trigger_training_event(call: CallStateModel) -> None:
    """
    Shortcut to add training to the queue.
    """
    await _training_queue.send_message(call.model_dump_json(exclude_none=True))


async def _trigger_post_event(call: CallStateModel) -> None:
    """
    Shortcut to add post-call intelligence to the queue.
    """
    await _post_queue.send_message(str(call.call_id))


async def _communicationservices_urls(
    phone_number: PhoneNumber, initiate: CallInitiateModel | None = None
) -> tuple[str, str, CallStateModel]:
    """
    Generate the callback URL for a call.

    If the caller has already called, use the same call ID, to keep the conversation history. Otherwise, create a new call ID.

    Returnes a tuple of the callback URL, the WebSocket URL, and the call object.
    """
    # Get call
    call = await _db.call_search_one(phone_number)

    # Create new call if initiate is different
    if not call or (initiate and call.initiate != initiate):
        call = await _db.call_create(
            CallStateModel(
                initiate=initiate
                or CallInitiateModel(
                    **CONFIG.conversation.initiate.model_dump(),
                    phone_number=phone_number,
                )
            )
        )

    # Format URLs
    wss_url = _COMMUNICATIONSERVICES_WSS_TPL.format(
        callback_secret=call.callback_secret,
        call_id=str(call.call_id),
    )
    callaback_url = _COMMUNICATIONSERVICES_CALLABACK_TPL.format(
        callback_secret=call.callback_secret,
        call_id=str(call.call_id),
    )

    return callaback_url, wss_url, call


# TODO: Secure this endpoint with a secret, either in the Authorization header or in the URL
@api.post(
    "/twilio/sms",
    status_code=HTTPStatus.OK,
)
@start_as_current_span("twilio_sms_post")
async def twilio_sms_post(
    Body: Annotated[str, Form()],
    From: Annotated[PhoneNumber, Form()],
) -> Response:
    """
    Handle incoming SMS event from Twilio.

    The event will trigger the workflow to handle a new SMS message.

    Returns a 200 OK if the SMS is properly formatted. Otherwise, returns a 400 Bad Request.
    """
    # Enrich span
    SpanAttributeEnum.CALL_PHONE_NUMBER.attribute(From)

    async with get_scheduler() as scheduler:
        # Get call
        call = await _db.call_search_one(
            callback_timeout=False,
            phone_number=From,
        )

        # Call not found
        if not call:
            logger.warning("Call for phone number %s not found", From)

        # Call found
        else:
            # Enrich span
            SpanAttributeEnum.CALL_ID.attribute(str(call.call_id))

            # Execute business logic
            event_status = await on_sms_received(
                call=call,
                message=Body,
                scheduler=scheduler,
            )

            # Return error for unsuccessful event
            if not event_status:
                raise HTTPException(
                    detail="SMS event failed",
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )

    # Default response
    return Response(
        content=str(MessagingResponse()),  # Twilio expects an empty response every time
        media_type="application/xml",
        status_code=HTTPStatus.OK,
    )


@api.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,  # noqa: ARG001
    exc: StarletteHTTPException,
) -> JSONResponse:
    """
    Handle HTTP exceptions and return the error in a standard format.
    """
    return _standard_error(
        message=exc.detail,
        status_code=HTTPStatus(exc.status_code),
    )


@api.exception_handler(RequestValidationError)
@api.exception_handler(ValueError)
async def validation_exception_handler(
    request: Request,  # noqa: ARG001
    exc: RequestValidationError,
) -> JSONResponse:
    """
    Handle validation exceptions and return the error in a standard format.
    """
    return _validation_error(exc)


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


def _validation_error(e: ValidationError | Exception) -> JSONResponse:
    """
    Generate a standard validation error response.
    """
    messages = []
    if isinstance(e, ValidationError) or isinstance(e, ValidationException):
        messages = [
            str(x) for x in e.errors()
        ]  # Pydantic returns well formatted errors, use them
    elif isinstance(e, ValueError):
        messages = [str(e)]  # TODO: Could it expose sensitive information?
    return _standard_error(
        details=messages,
        message="Validation error",
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
    )


def _standard_error(
    message: str,
    status_code,
    details: list[str] | None = None,
) -> JSONResponse:
    """
    Generate a standard error response.
    """
    model = ErrorModel(
        error=ErrorInnerModel(
            details=details or [],
            message=message,
        )
    )
    return JSONResponse(
        content=model.model_dump(mode="json"),
        status_code=status_code,
    )


@lru_acache()
async def _use_automation_client() -> CallAutomationClient:
    """
    Get the call automation client for Azure Communication Services.

    Object is cached for performance.

    Returns a `CallAutomationClient` instance.
    """
    logger.debug(
        "Using Automation Client for %s", CONFIG.communication_services.endpoint
    )

    return CallAutomationClient(
        # Deployment
        endpoint=CONFIG.communication_services.endpoint,
        # Performance
        transport=await azure_transport(),
        # Authentication
        credential=AzureKeyCredential(
            CONFIG.communication_services.access_key.get_secret_value()
        ),  # Cannot place calls with RBAC, need to use access key (see: https://learn.microsoft.com/en-us/azure/communication-services/concepts/authentication#authentication-options)
    )
