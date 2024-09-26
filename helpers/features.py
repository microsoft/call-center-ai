from typing import TypeVar, cast

from azure.appconfiguration.aio import AzureAppConfigurationClient

from helpers.config import CONFIG
from helpers.config_models.cache import MemoryModel
from helpers.http import azure_transport
from persistence.icache import ICache
from persistence.memory import MemoryCache

_cache: ICache = MemoryCache(MemoryModel(max_size=100))
_client: AzureAppConfigurationClient | None = None
T = TypeVar("T", bool, int, str)


async def answer_hard_timeout_sec() -> int:
    return await _get(key="answer_hard_timeout_sec", type=int) or 180


async def answer_soft_timeout_sec() -> int:
    return await _get(key="answer_soft_timeout_sec", type=int) or 120


async def callback_timeout_hour() -> int:
    return await _get(key="callback_timeout_hour", type=int) or 24


async def phone_silence_timeout_sec() -> int:
    return await _get(key="phone_silence_timeout_sec", type=int) or 10


async def recording_enabled() -> bool:
    return await _get(key="recording_enabled", type=bool) or False


async def slow_llm_for_chat() -> bool:
    return await _get(key="slow_llm_for_chat", type=bool) or True


async def voice_recognition_retry_max() -> int:
    return await _get(key="voice_recognition_retry_max", type=int) or 3


async def _get(key: str, type: type[T]) -> T | None:
    # Try cache
    cache_key = _cache_key(key)
    cached = await _cache.aget(cache_key)
    if cached:
        return _parse(value=cached.decode(), type=type)
    # Try live
    async with await _use_client() as client:
        setting = await client.get_configuration_setting(key)
    # Return default if not found
    if not setting:
        return
    # Update cache
    await _cache.aset(
        key=cache_key,
        ttl_sec=CONFIG.app_configuration.ttl_sec,
        value=setting.value,
    )
    # Return
    return _parse(value=setting.value, type=type)


async def _use_client() -> AzureAppConfigurationClient:
    """
    Generate the App Configuration client and close it after use.
    """
    global _client  # noqa: PLW0603
    if not _client:
        _client = AzureAppConfigurationClient.from_connection_string(
            # Performance
            transport=await azure_transport(),
            # Authentication
            connection_string=CONFIG.app_configuration.connection_string.get_secret_value(),
        )
    return _client


def _cache_key(key: str) -> str:
    return f"{__name__}-{key}"


def _parse(value: str, type: type[T]) -> T:
    if type is bool:
        return cast(T, value.lower() == "true")
    if type is int:
        return cast(T, int(value))
    if type is str:
        return cast(T, str(value))
    raise ValueError(f"Unsupported type: {type}")
