from helpers.config import CONFIG
import logging


LOGGING_APP_LEVEL = CONFIG.monitoring.logging.app_level.value
LOGGING_SYS_LEVEL = CONFIG.monitoring.logging.sys_level.value

logging.basicConfig(level=LOGGING_SYS_LEVEL)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(LOGGING_APP_LEVEL)
    return logger
