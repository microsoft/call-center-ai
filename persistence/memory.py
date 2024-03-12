from helpers.config_models.cache import MemoryModel
from helpers.logging import build_logger
from persistence.icache import ICache
from typing import Dict, Optional, Union


_logger = build_logger(__name__)


class MemoryCache(ICache):
    _config: MemoryModel
    _cache: Dict[str, Union[bytes, None]] = {}

    def __init__(self, config: MemoryModel):
        _logger.info(f"Using memory cache with {config.max_size} size limit")
        self._config = config

    async def aget(self, key: Union[str, bytes]) -> Optional[bytes]:
        """
        Get a value from the cache.

        If the key does not exist, return `None`.
        """
        str_key = self._key_to_str(key)
        return self._cache.get(str_key, None)

    async def aset(
        self, key: Union[str, bytes], value: Union[str, bytes, None]
    ) -> bool:
        """
        Set a value in the cache.

        If the value is `None`, set an empty string. If the cache is full, delete the cache and start over.
        """
        if len(self._cache) >= self._config.max_size:
            self._cache = {}
        str_key = self._key_to_str(key)
        self._cache[str_key] = value.encode() if isinstance(value, str) else value
        return True

    @staticmethod
    def _key_to_str(key: Union[str, bytes]) -> str:
        return key.decode() if isinstance(key, bytes) else str(key)
