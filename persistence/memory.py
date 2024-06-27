from collections import OrderedDict
from helpers.config_models.cache import MemoryModel
from helpers.logging import logger
from models.readiness import ReadinessEnum
from persistence.icache import ICache
from typing import Optional, Union
import hashlib


class MemoryCache(ICache):
    """
    A simple in-memory cache.

    Use the least recently used (LRU) policy to remove the oldest used items when the cache is full.

    See: https://en.wikipedia.org/wiki/Cache_replacement_policies#Least_recently_used_(LRU)
    """

    _cache: OrderedDict[str, Union[bytes, None]] = OrderedDict()
    _config: MemoryModel

    def __init__(self, config: MemoryModel):
        logger.warning(
            f"Using memory cache with {config.max_size} size limit, memory usage can be high, prefer an external cache like Redis"
        )
        self._config = config

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the memory cache.
        """
        return ReadinessEnum.OK  # Always ready, it's memory :)

    async def aget(self, key: str) -> Optional[bytes]:
        """
        Get a value from the cache.

        If the key does not exist, return `None`.
        """
        sha_key = self._key_to_hash(key)
        res = self._cache.get(sha_key, None)
        if not res:
            return None
        self._cache.move_to_end(sha_key, last=False)  # Move to first
        return res

    async def aset(self, key: str, value: Union[str, bytes, None]) -> bool:
        """
        Set a value in the cache.
        """
        sha_key = self._key_to_hash(key)
        if len(self._cache) >= self._config.max_size:
            self._cache.popitem()  # Delete the last
        # Add to first
        self._cache[sha_key] = value.encode() if isinstance(value, str) else value
        self._cache.move_to_end(sha_key, last=False)
        return True

    async def adel(self, key: str) -> bool:
        """
        Delete a value from the cache.
        """
        sha_key = self._key_to_hash(key)
        if sha_key in self._cache:
            self._cache.pop(sha_key)
        return True

    @staticmethod
    def _key_to_hash(key: str) -> str:
        """
        Transform the key into a hash.

        SHA-256 lower the collision probability. Plus, it reduce the key size, which is useful for memory usage.
        """
        return hashlib.sha256(key.encode(), usedforsecurity=False).hexdigest()
