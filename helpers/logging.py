from azure.monitor.opentelemetry import configure_azure_monitor
from helpers.config import CONFIG
from logging import Logger, getLogger, basicConfig
from opentelemetry import trace
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from os import environ


# Logger
basicConfig(level=CONFIG.monitoring.logging.sys_level.value)
logger = getLogger("call-center-ai")
logger.setLevel(CONFIG.monitoring.logging.app_level.value)

# OpenTelemetry
environ["OTEL_TRACES_SAMPLER_ARG"] = str(0.5)  # Sample 50% of traces
configure_azure_monitor(
    connection_string=CONFIG.monitoring.application_insights.connection_string.get_secret_value(),
)  # Configure Azure monitor collection
AioHttpClientInstrumentor().instrument()  # Instrument aiohttp
HTTPXClientInstrumentor().instrument()  # Instrument httpx
tracer = trace.get_tracer(
    instrumenting_library_version=CONFIG.version,
    instrumenting_module_name="com.github.clemlesne.call-center-ai",
)  # Create a tracer that will be used in the app
