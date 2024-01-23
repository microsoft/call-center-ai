from helpers.config import CONFIG
from logging import Logger, getLogger, basicConfig


LOGGING_APP_LEVEL = CONFIG.monitoring.logging.app_level.value
LOGGING_SYS_LEVEL = CONFIG.monitoring.logging.sys_level.value

basicConfig(level=LOGGING_SYS_LEVEL)


def build_logger(name: str) -> Logger:
    logger = getLogger(name)
    logger.setLevel(LOGGING_APP_LEVEL)
    return logger
