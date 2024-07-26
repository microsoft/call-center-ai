from os import environ

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.trace.span import INVALID_SPAN
from opentelemetry.util.types import AttributeValue

VERSION = environ.get("VERSION", "0.0.0-unknown")

try:
    configure_azure_monitor()  # Configure Azure Application Insights exporter
    AioHttpClientInstrumentor().instrument()  # Instrument aiohttp
    HTTPXClientInstrumentor().instrument()  # Instrument httpx
except ValueError as e:
    print(
        "Azure Application Insights instrumentation failed, likely due to a missing APPLICATIONINSIGHTS_CONNECTION_STRING environment variable.",
        e,
    )

tracer = trace.get_tracer(
    instrumenting_library_version=VERSION,
    instrumenting_module_name="com.github.clemlesne.call-center-ai",
)  # Create a tracer that will be used in the app


def span_attribute(key: str, value: AttributeValue) -> None:
    """
    Set an attribute on the current span.

    Prefer using attributes from `opentelemetry.semconv.attributes` when possible.

    Returns None.
    """
    span = trace.get_current_span()
    if span == INVALID_SPAN:
        return
    span.set_attribute(key, value)


class CallAttributes:
    """
    OpenTelemetry attributes for a call.

    These attributes are used to track a call in the logs and metrics.
    """

    CALL_CHANNEL = "call.channel"
    CALL_ID = "call.id"
    CALL_MESSAGE = "call.message"
    CALL_PHONE_NUMBER = "call.phone_number"
