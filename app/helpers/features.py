from typing import TypeVar, cast

from azure.appconfiguration.aio import AzureAppConfigurationClient
from azure.core.exceptions import ResourceNotFoundError

from app.helpers.config import CONFIG
from app.helpers.config_models.cache import MemoryModel
from app.helpers.http import azure_transport
from app.helpers.identity import credential
from app.helpers.logging import logger
from app.persistence.icache import ICache
from app.persistence.memory import MemoryCache

_cache: ICache = MemoryCache(MemoryModel(max_size=100))
_client: AzureAppConfigurationClient | None = None
T = TypeVar("T", bool, int, float, str)


async def answer_hard_timeout_sec() -> int:
    return await _get(key="answer_hard_timeout_sec", type_res=int) or 180


async def answer_soft_timeout_sec() -> int:
    return await _get(key="answer_soft_timeout_sec", type_res=int) or 120


async def callback_timeout_hour() -> int:
    return await _get(key="callback_timeout_hour", type_res=int) or 24


async def vad_silence_timeout_ms() -> int:
    return await _get(key="vad_silence_timeout_ms", type_res=int) or 500


async def vad_threshold() -> float:
    return await _get(key="vad_threshold", type_res=float) or 0.5


async def recording_enabled() -> bool:
    return await _get(key="recording_enabled", type_res=bool) or False


async def recognition_retry_max() -> int:
    return await _get(key="recognition_retry_max", type_res=int) or 3


async def _get(key: str, type_res: type[T]) -> T | None:
    # Try cache
    cache_key = _cache_key(key)
    cached = await _cache.aget(cache_key)
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
    await _cache.aset(
        key=cache_key,
        ttl_sec=CONFIG.app_configuration.ttl_sec,
        value=setting.value,
    )
    # Return
    return _parse(value=setting.value, type_res=type_res)


async def _use_client() -> AzureAppConfigurationClient:
    """
    Generate the App Configuration client and close it after use.
    """
    global _client  # noqa: PLW0603
    if not _client:
        _client = AzureAppConfigurationClient(
            # Performance
            transport=await azure_transport(),
            # Deployment
            base_url=CONFIG.app_configuration.endpoint,
            # Authentication
            credential=await credential(),
        )
    return _client


def _cache_key(key: str) -> str:
    return f"{__name__}-{key}"


def _parse(value: str, type_res: type[T]) -> T | None:
    try:
        if type_res is bool:
            return cast(T, value.lower() == "true")
        if type_res is int:
            return cast(T, int(value))
        if type_res is float:
            return cast(T, float(value))
        if type_res is str:
            return cast(T, str(value))
        raise ValueError(f"Unsupported type: {type_res}")
    except ValueError:
        pass
