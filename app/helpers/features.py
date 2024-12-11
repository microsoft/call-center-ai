from typing import TypeVar, cast

from azure.appconfiguration.aio import AzureAppConfigurationClient
from azure.core.exceptions import ResourceNotFoundError

from app.helpers.cache import async_lru_cache
from app.helpers.config import CONFIG
from app.helpers.config_models.cache import MemoryModel
from app.helpers.http import azure_transport
from app.helpers.identity import credential
from app.helpers.logging import logger
from app.persistence.icache import ICache
from app.persistence.memory import MemoryCache

_cache: ICache = MemoryCache(MemoryModel(max_size=100))
T = TypeVar("T", bool, int, float, str)


async def answer_hard_timeout_sec() -> int:
    return await _default(
        default=180,
        key="answer_hard_timeout_sec",
        type_res=int,
    )


async def answer_soft_timeout_sec() -> int:
    return await _default(
        default=120,
        key="answer_soft_timeout_sec",
        type_res=int,
    )


async def callback_timeout_hour() -> int:
    return await _default(
        default=24,
        key="callback_timeout_hour",
        type_res=int,
    )


async def phone_silence_timeout_sec() -> int:
    return await _default(
        default=20,
        key="phone_silence_timeout_sec",
        type_res=int,
    )


async def vad_threshold() -> float:
    return await _default(
        default=0.5,
        key="vad_threshold",
        type_res=float,
    )


async def vad_silence_timeout_ms() -> int:
    return await _default(
        default=500,
        key="vad_silence_timeout_ms",
        type_res=int,
    )


async def vad_cutoff_timeout_ms() -> int:
    return await _default(
        default=150,
        key="vad_cutoff_timeout_ms",
        type_res=int,
    )


async def recording_enabled() -> bool:
    return await _default(
        default=False,
        key="recording_enabled",
        type_res=bool,
    )


async def slow_llm_for_chat() -> bool:
    return await _default(
        default=True,
        key="slow_llm_for_chat",
        type_res=bool,
    )


async def recognition_retry_max() -> int:
    return await _default(
        default=3,
        key="recognition_retry_max",
        type_res=int,
    )


async def _default(
    default: T,
    key: str,
    type_res: type[T],
) -> T:
    """
    Get a setting from the App Configuration service with a default value.
    """
    return (await _get(key=key, type_res=type_res)) or default


async def _get(
    key: str,
    type_res: type[T],
) -> T | None:
    """
    Get a setting from the App Configuration service.
    """
    # Try cache
    cache_key = _cache_key(key)
    cached = await _cache.get(cache_key)
    if cached:
        return _parse(value=cached.decode(), type_res=type_res)
    # Try live
    try:
        async with await _use_client() as client:
            setting = await client.get_configuration_setting(key)
        # Return default if not found
        if not setting:
            return
    except ResourceNotFoundError:
        logger.warning("Setting %s not found", key)
        return
    # Update cache
    await _cache.set(
        key=cache_key,
        ttl_sec=CONFIG.app_configuration.ttl_sec,
        value=setting.value,
    )
    # Return
    return _parse(value=setting.value, type_res=type_res)


@async_lru_cache()
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
