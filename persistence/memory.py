import hashlib
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from helpers.config_models.cache import MemoryModel
from helpers.logging import logger
from models.readiness import ReadinessEnum
from persistence.icache import ICache


class MemoryCache(ICache):
    """
    A simple in-memory cache.

    Use the least recently used (LRU) policy to remove the oldest used items when the cache is full.

    See: https://en.wikipedia.org/wiki/Cache_replacement_policies#Least_recently_used_(LRU)
    """

    _cache: OrderedDict[str, bytes | None] = OrderedDict()
    _config: MemoryModel
    _ttl: dict[str, datetime] = {}

    def __init__(self, config: MemoryModel):
        logger.warning(
            "Using memory cache with %s size limit, memory usage can be high, prefer an external cache like Redis",
            config.max_size,
        )
        self._config = config

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the memory cache.
        """
        return ReadinessEnum.OK  # Always ready, it's memory :)

    async def aget(self, key: str) -> bytes | None:
        """
        Get a value from the cache.

        If the key does not exist, return `None`.
        """
        sha_key = self._key_to_hash(key)
        # Check TTL
        if sha_key in self._ttl:
            if self._ttl[sha_key] < datetime.now(UTC):
                return None
        # Get from cache
        res = self._cache.get(sha_key, None)
        if not res:
            return None
        # Move to first
        self._cache.move_to_end(sha_key, last=False)
        return res

    async def aset(self, key: str, value: str | bytes | None, ttl_sec: int) -> bool:
        """
        Set a value in the cache.
        """
        sha_key = self._key_to_hash(key)
        # Delete the last if full
        if len(self._cache) >= self._config.max_size:
            self._cache.popitem()
        # Add to first
        self._cache[sha_key] = value.encode() if isinstance(value, str) else value
        self._cache.move_to_end(sha_key, last=False)
        # Set the TTL
        self._ttl[sha_key] = datetime.now(UTC) + timedelta(seconds=ttl_sec)
        return True

    async def adel(self, key: str) -> bool:
        """
        Delete a value from the cache.
        """
        sha_key = self._key_to_hash(key)
        # Delete from cache
        if sha_key in self._cache:
            self._cache.pop(sha_key)
        # Delete from TTL
        if sha_key in self._ttl:
            self._ttl.pop(sha_key)
        return True

    @staticmethod
    def _key_to_hash(key: str) -> str:
        """
        Transform the key into a hash.

        SHA-256 lower the collision probability. Plus, it reduce the key size, which is useful for memory usage.
        """
        return hashlib.sha256(key.encode(), usedforsecurity=False).hexdigest()
