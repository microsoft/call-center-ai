from azure.monitor.opentelemetry import configure_azure_monitor
from helpers.config import CONFIG
from logging import getLogger, basicConfig
from opentelemetry import trace
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor


APP_NAME = "call-center-ai"

# Logger
basicConfig(level=CONFIG.monitoring.logging.sys_level.value)
logger = getLogger(APP_NAME)
logger.setLevel(CONFIG.monitoring.logging.app_level.value)

# OpenTelemetry
configure_azure_monitor()  # Configure Azure Application Insights exporter
AioHttpClientInstrumentor().instrument()  # Instrument aiohttp
HTTPXClientInstrumentor().instrument()  # Instrument httpx
tracer = trace.get_tracer(
    instrumenting_library_version=CONFIG.version,
    instrumenting_module_name=f"com.github.clemlesne.{APP_NAME}",
)  # Create a tracer that will be used in the app
