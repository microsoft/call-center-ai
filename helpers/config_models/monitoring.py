from enum import Enum
from pydantic_settings import BaseSettings


class LoggingLevel(str, Enum):
    # Copied from https://docs.python.org/3.12/library/logging.html#logging-levels
    CRITICAL = "CRITICAL"
    DEBUG = "DEBUG"
    ERROR = "ERROR"
    INFO = "INFO"
    WARN = "WARN"  # Alias for WARNING, non-standard but used by the logging module
    WARNING = "WARNING"


class LoggingMonitoringModel(BaseSettings):
    app_level: LoggingLevel = LoggingLevel.INFO
    sys_level: LoggingLevel = LoggingLevel.WARNING


class MonitoringModel(BaseSettings):
    logging: LoggingMonitoringModel = (
        LoggingMonitoringModel()
    )  # Object is fully defined by default
