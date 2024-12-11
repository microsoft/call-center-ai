from logging import Logger, _nameToLevel, basicConfig

from structlog import (
    configure_once,
    get_logger as structlog_get_logger,
    make_filtering_bound_logger,
)
from structlog.contextvars import merge_contextvars
from structlog.dev import ConsoleRenderer
from structlog.processors import (
    StackInfoRenderer,
    TimeStamper,
    UnicodeDecoder,
    add_log_level,
)
from structlog.stdlib import PositionalArgumentsFormatter

from app.helpers.config import CONFIG

# Default logging level for all the dependencies
basicConfig(level=CONFIG.monitoring.logging.sys_level.value)

# Configure application console logging
configure_once(
    cache_logger_on_first_use=True,
    context_class=dict,
    wrapper_class=make_filtering_bound_logger(
        _nameToLevel[CONFIG.monitoring.logging.app_level.value]
    ),
    processors=[
        # Add contextvars support
        merge_contextvars,
        # Add log level
        add_log_level,
        # Enable %s-style formatting
        PositionalArgumentsFormatter(),
        # Add timestamp
        TimeStamper(fmt="iso", utc=True),
        # Add exceptions info
        StackInfoRenderer(),
        # Decode Unicode to str
        UnicodeDecoder(),
        # Pretty printing in a terminal session
        ConsoleRenderer(),
    ],
)

# Framework does not exactly expose Logger, but that's easier to work with
logger: Logger = structlog_get_logger("call-center-ai")
