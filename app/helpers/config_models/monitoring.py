from enum import Enum

from pydantic import BaseModel


class LoggingLevelEnum(str, Enum):
    # Copied from https://docs.python.org/3.13/library/logging.html#logging-levels
    CRITICAL = "CRITICAL"
    DEBUG = "DEBUG"
    ERROR = "ERROR"
    INFO = "INFO"
    WARN = "WARN"  # Alias for WARNING, non-standard but used by the logging module
    WARNING = "WARNING"


class LoggingModel(BaseModel):
    app_level: LoggingLevelEnum = LoggingLevelEnum.INFO
    sys_level: LoggingLevelEnum = LoggingLevelEnum.WARNING


class MonitoringModel(BaseModel):
    logging: LoggingModel = LoggingModel()  # Object is fully defined by default
