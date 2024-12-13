from enum import Enum
from os import environ

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.metrics._internal.instrument import Counter, Gauge
from opentelemetry.trace.span import INVALID_SPAN
from opentelemetry.util.types import AttributeValue
from structlog.contextvars import bind_contextvars, get_contextvars

MODULE_NAME = "com.github.clemlesne.call-center-ai"
VERSION = environ.get("VERSION", "0.0.0-unknown")


class SpanAttributeEnum(str, Enum):
    """
    OpenTelemetry attributes.

    These attributes are used to track a call in the logs and metrics.
    """

    CALL_CHANNEL = "call.channel"
    """Message channel (e.g. sms, ivr, ...)."""
    CALL_ID = "call.id"
    """Technical call identifier."""
    CALL_MESSAGE = "call.message"
    """Message content as a string."""
    CALL_PHONE_NUMBER = "call.phone_number"
    """Phone number of the caller."""
    TOOL_ARGS = "tool.args"
    """Tool arguments being used."""
    TOOL_NAME = "tool.name"
    """Tool name being used."""
    TOOL_RESULT = "tool.result"
    """Tool result."""

    def attribute(
        self,
        value: AttributeValue,
    ) -> None:
        """
        Set an attribute on the current span.
        """
        # Enrich logging
        bind_contextvars(**{self.value: value})

        # Enrich span
        span = trace.get_current_span()
        if span == INVALID_SPAN:
            return
        span.set_attribute(self.value, value)


class SpanCounterEnum(str, Enum):
    CALL_ANSWER_LATENCY = "call.answer.latency"
    """Answer latency in seconds."""
    CALL_AEC_MISSED = "call.aec.missed"
    """Echo cancellation missed frames."""
    CALL_AEC_DROPED = "call.aec.droped"
    """Echo cancellation dropped frames."""

    def counter(
        self,
        unit: str,
    ) -> Counter:
        """
        Create a counter metric to track a span counter.
        """
        return meter.create_counter(
            description=self.__doc__ or "",
            name=self.value,
            unit=unit,
        )

    def gauge(
        self,
        unit: str,
    ) -> Gauge:
        """
        Create a gauge metric to track a span counter.
        """
        return meter.create_gauge(
            description=self.__doc__ or "",
            name=self.value,
            unit=unit,
        )


try:
    configure_azure_monitor()  # Configure Azure Application Insights exporter
    AioHttpClientInstrumentor().instrument()  # Instrument aiohttp
    HTTPXClientInstrumentor().instrument()  # Instrument httpx
except ValueError as e:
    print(  # noqa: T201
        "Azure Application Insights instrumentation failed, likely due to a missing APPLICATIONINSIGHTS_CONNECTION_STRING environment variable.",
        e,
    )

# Create a tracer and meter that will be used across the application
tracer = trace.get_tracer(
    instrumenting_library_version=VERSION,
    instrumenting_module_name=MODULE_NAME,
)
meter = metrics.get_meter(
    name=MODULE_NAME,
    version=VERSION,
)

# Init metrics
call_answer_latency = SpanCounterEnum.CALL_ANSWER_LATENCY.gauge("s")
call_aec_droped = SpanCounterEnum.CALL_AEC_DROPED.counter("frames")
call_aec_missed = SpanCounterEnum.CALL_AEC_MISSED.counter("frames")


def gauge_set(
    metric: Gauge,
    value: float | int,
):
    """
    Set a gauge metric value with context attributes.
    """
    metric.set(
        amount=value,
        attributes=get_contextvars(),
    )


def counter_add(
    metric: Counter,
    value: float | int,
):
    """
    Add a counter metric value with context attributes.
    """
    metric.add(
        amount=value,
        attributes=get_contextvars(),
    )
