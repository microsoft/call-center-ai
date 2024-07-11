from os import environ

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

VERSION = environ.get("VERSION", "0.0.0-unknown")

configure_azure_monitor()  # Configure Azure Application Insights exporter
AioHttpClientInstrumentor().instrument()  # Instrument aiohttp
HTTPXClientInstrumentor().instrument()  # Instrument httpx
tracer = trace.get_tracer(
    instrumenting_library_version=VERSION,
    instrumenting_module_name="com.github.clemlesne.call-center-ai",
)  # Create a tracer that will be used in the app
