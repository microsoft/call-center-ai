from logging import StreamHandler, basicConfig, getLogger

from app.helpers.config import CONFIG

basicConfig(level=CONFIG.monitoring.logging.sys_level.value)
getLogger("").addHandler(StreamHandler())
logger = getLogger("call-center-ai")
logger.setLevel(CONFIG.monitoring.logging.app_level.value)
