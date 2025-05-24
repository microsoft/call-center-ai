from typing import TypeVar, cast

from azure.appconfiguration.aio import AzureAppConfigurationClient
from azure.core.exceptions import ResourceNotFoundError

from app.helpers.cache import lru_acache
from app.helpers.config import CONFIG
from app.helpers.config_models.cache import MemoryModel
from app.helpers.http import azure_transport
from app.helpers.identity import credential
from app.helpers.logging import logger
from app.persistence.memory import MemoryCache

_cache = MemoryCache(MemoryModel())
T = TypeVar("T", bool, int, float, str)


async def answer_hard_timeout_sec() -> int:
    """
    Time waiting the LLM before aborting the answer with an error message.
    """
    return await _default(
        default=15,
        key="answer_hard_timeout_sec",
        type_res=int,
    )


async def answer_soft_timeout_sec() -> int:
    """
    Time waiting the LLM before sending a waiting message.
    """
    return await _default(
        default=3,
        key="answer_soft_timeout_sec",
        type_res=int,
    )


async def callback_timeout_hour() -> int:
    """
    The timeout for a callback in hours. Set 0 to disable.
    """
    return await _default(
        default=24,
        key="callback_timeout_hour",
        type_res=int,
    )


async def phone_silence_timeout_sec() -> int:
    """
    Amount of silence in secs to trigger a warning message from the assistant.
    """
    return await _default(
        default=20,
        key="phone_silence_timeout_sec",
        type_res=int,
    )


async def vad_threshold() -> float:
    """
    The threshold for voice activity detection. Between 0.1 and 1.
    """
    return await _default(
        default=0.5,
        key="vad_threshold",
        max_incl=1,
        min_incl=0.1,
        type_res=float,
    )


async def vad_silence_timeout_ms() -> int:
    """
    Silence to trigger voice activity detection in milliseconds.
    """
    return await _default(
        default=500,
        key="vad_silence_timeout_ms",
        type_res=int,
    )


async def vad_cutoff_timeout_ms() -> int:
    """
    The cutoff timeout for voice activity detection in milliseconds.
    """
    return await _default(
        default=250,
        key="vad_cutoff_timeout_ms",
        type_res=int,
    )


async def recording_enabled() -> bool:
    """
    Whether call recording is enabled.
    """
    return await _default(
        default=False,
        key="recording_enabled",
        type_res=bool,
    )


async def slow_llm_for_chat() -> bool:
    """
    Whether to use the slow LLM for chat.
    """
    return await _default(
        default=True,
        key="slow_llm_for_chat",
        type_res=bool,
    )


async def recognition_retry_max() -> int:
    """
    The maximum number of retries for voice recognition. Minimum of 1.
    """
    return await _default(
        default=3,
        key="recognition_retry_max",
        min_incl=1,
        type_res=int,
    )


async def recognition_stt_complete_timeout_ms() -> int:
    """
    The timeout for STT completion in milliseconds.
    """
    return await _default(
        default=100,
        key="recognition_stt_complete_timeout_ms",
        type_res=int,
    )


async def _default(
    default: T,
    key: str,
    type_res: type[T],
    max_incl: T | None = None,
    min_incl: T | None = None,
) -> T:
    """
    Get a setting from the App Configuration service with a default value.
    """
    # Get the setting
    res = await _get(
        key=key,
        type_res=type_res,
    )
    if res:
        return _validate(
            key=key,
            max_incl=max_incl,
            min_incl=min_incl,
            res=res,
        )

    # Return default
    logger.info("Feature %s not found, using default: %s", key, default)
    return _validate(
        key=key,
        max_incl=max_incl,
        min_incl=min_incl,
        res=default,
    )


def _validate(
    key: str,
    res: T,
    max_incl: T | None = None,
    min_incl: T | None = None,
) -> T:
    """
    Validate a setting value against min and max.
    """
    # Check min
    if min_incl is not None and res < min_incl:
        logger.warning("Feature %s is below min: %s", key, res)
        return min_incl
    # Check max
    if max_incl is not None and res > max_incl:
        logger.warning("Feature %s is above max: %s", key, res)
        return max_incl
    # Return value
    return res


async def _get(key: str, type_res: type[T]) -> T | None:
    """
    Get a setting from the App Configuration service.
    """
    # Try cache
    cache_key = _cache_key(key)
    cached = await _cache.get(cache_key)
    if cached:
        return _parse(
            type_res=type_res,
            value=cached.decode(),
        )

    # Try live
    try:
        async with await _use_client() as client:
            setting = await client.get_configuration_setting(key)
        # Return default if not found
        if not setting:
            return
        res = setting.value
    except ResourceNotFoundError:
        return

    logger.debug("Setting %s refreshed: %s", key, res)

    # Update cache
    await _cache.set(
        key=cache_key,
        ttl_sec=CONFIG.app_configuration.ttl_sec,
        value=res,
    )

    # Return value
    return _parse(
        type_res=type_res,
        value=res,
    )


@lru_acache()
async def _use_client() -> AzureAppConfigurationClient:
    """
    Generate the App Configuration client and close it after use.
    """
    logger.debug(
        "Using App Configuration client for %s", CONFIG.app_configuration.endpoint
    )

    return AzureAppConfigurationClient(
        # Performance
        transport=await azure_transport(),
        # Deployment
        base_url=CONFIG.app_configuration.endpoint,
        # Authentication
        credential=await credential(),
    )


def _cache_key(key: str) -> str:
    """
    Generate a cache key for a setting.
    """
    return f"{__name__}-{key}"


def _parse(value: str, type_res: type[T]) -> T | None:
    """
    Parse a setting value to a type.

    Supported types: bool, int, float, str.
    """
    # Try parse
    if type_res is bool:
        return cast(T, value.lower() == "true")
    if type_res is int:
        return cast(T, int(value))
    if type_res is float:
        return cast(T, float(value))
    if type_res is str:
        return cast(T, str(value))

    # Unsupported type
    logger.error("Unsupported feature type: %s", type_res)
    return
