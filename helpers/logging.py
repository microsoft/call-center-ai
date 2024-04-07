from azure.monitor.opentelemetry import configure_azure_monitor
from helpers.config import CONFIG
from logging import Logger, getLogger, basicConfig, StreamHandler
from opentelemetry import trace
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor


configure_azure_monitor(
    connection_string=CONFIG.monitoring.application_insights.connection_string.get_secret_value(),
)  # Configure Azure monitor collection
getLogger("").addHandler(StreamHandler())  # Re-enable logging to stdout
AioHttpClientInstrumentor().instrument()  # Instrument aiohttp
HTTPXClientInstrumentor().instrument()  # Instrument httpx

_LOGGING_APP_LEVEL = CONFIG.monitoring.logging.app_level.value
_LOGGING_SYS_LEVEL = CONFIG.monitoring.logging.sys_level.value

basicConfig(level=_LOGGING_SYS_LEVEL)

# Creates a tracer from the global tracer provider
TRACER = trace.get_tracer(
    instrumenting_library_version=CONFIG.version,
    instrumenting_module_name="com.github.clemlesne.claim-ai",
)


def build_logger(name: str) -> Logger:
    logger = getLogger(name)
    logger.setLevel(_LOGGING_APP_LEVEL)
    return logger
