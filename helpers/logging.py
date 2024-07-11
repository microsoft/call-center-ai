from logging import basicConfig, getLogger

from helpers.config import CONFIG

basicConfig(level=CONFIG.monitoring.logging.sys_level.value)
logger = getLogger("call-center-ai")
logger.setLevel(CONFIG.monitoring.logging.app_level.value)
