from typing import TypeVar, cast

from aiojobs import Scheduler
from azure.appconfiguration.aio import AzureAppConfigurationClient
from azure.core.exceptions import ResourceNotFoundError

from app.helpers.cache import async_lru_cache
from app.helpers.config import CONFIG
from app.helpers.config_models.cache import MemoryModel
from app.helpers.http import azure_transport
from app.helpers.identity import credential
from app.helpers.logging import logger
from app.persistence.memory import MemoryCache

_cache = MemoryCache(MemoryModel())
T = TypeVar("T", bool, int, float, str)


async def answer_hard_timeout_sec(scheduler: Scheduler) -> int:
    return await _default(
        default=180,
        key="answer_hard_timeout_sec",
        scheduler=scheduler,
        type_res=int,
    )


async def answer_soft_timeout_sec(scheduler: Scheduler) -> int:
    return await _default(
        default=120,
        key="answer_soft_timeout_sec",
        scheduler=scheduler,
        type_res=int,
    )


async def callback_timeout_hour(scheduler: Scheduler) -> int:
    return await _default(
        default=24,
        key="callback_timeout_hour",
        scheduler=scheduler,
        type_res=int,
    )


async def phone_silence_timeout_sec(scheduler: Scheduler) -> int:
    return await _default(
        default=20,
        key="phone_silence_timeout_sec",
        scheduler=scheduler,
        type_res=int,
    )


async def vad_threshold(scheduler: Scheduler) -> float:
    return await _default(
        default=0.5,
        key="vad_threshold",
        scheduler=scheduler,
        type_res=float,
    )


async def vad_silence_timeout_ms(scheduler: Scheduler) -> int:
    return await _default(
        default=500,
        key="vad_silence_timeout_ms",
        scheduler=scheduler,
        type_res=int,
    )


async def vad_cutoff_timeout_ms(scheduler: Scheduler) -> int:
    return await _default(
        default=150,
        key="vad_cutoff_timeout_ms",
        scheduler=scheduler,
        type_res=int,
    )


async def recording_enabled(scheduler: Scheduler) -> bool:
    return await _default(
        default=False,
        key="recording_enabled",
        scheduler=scheduler,
        type_res=bool,
    )


async def slow_llm_for_chat(scheduler: Scheduler) -> bool:
    return await _default(
        default=True,
        key="slow_llm_for_chat",
        scheduler=scheduler,
        type_res=bool,
    )


async def recognition_retry_max(scheduler: Scheduler) -> int:
    return await _default(
        default=3,
        key="recognition_retry_max",
        scheduler=scheduler,
        type_res=int,
    )


async def _default(
    default: T,
    key: str,
    scheduler: Scheduler,
    type_res: type[T],
) -> T:
    """
    Get a setting from the App Configuration service with a default value.
    """
    # Get the setting
    res = await _get(
        key=key,
        scheduler=scheduler,
        type_res=type_res,
    )
    if res:
        return res

    # Return default
    logger.info("Feature %s not found, using default: %s", key, default)
    return default


async def _get(
    key: str,
    scheduler: Scheduler,
    type_res: type[T],
) -> T | None:
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

    # Defer the update
    await scheduler.spawn(_refresh(cache_key, key))
    return


async def _refresh(
    cache_key: str,
    key: str,
) -> T | None:
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
