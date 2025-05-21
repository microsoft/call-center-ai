import hashlib
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from app.helpers.config_models.cache import MemoryModel
from app.helpers.monitoring import suppress
from app.models.readiness import ReadinessEnum
from app.persistence.icache import ICache


class MemoryCache(ICache):
    """
    A simple in-memory cache.

    Use the least recently used (LRU) policy to remove the oldest used items when the cache is full.

    See: https://en.wikipedia.org/wiki/Cache_replacement_policies#Least_recently_used_(LRU)
    """

    _cache: OrderedDict[str, bytes | None] = OrderedDict()
    _config: MemoryModel
    _ttl: OrderedDict[str, datetime] = OrderedDict()

    def __init__(self, config: MemoryModel):
        self._config = config

    async def readiness(self) -> ReadinessEnum:
        """
        Check the readiness of the memory cache.
        """
        return ReadinessEnum.OK  # Always ready, it's memory :)

    async def get(self, key: str) -> bytes | None:
        """
        Get a value from the cache.

        If the key does not exist, return `None`.
        """
        sha_key = self._key_to_hash(key)

        # Check TTL, delete if expired
        ttl = self._ttl.get(sha_key, None)
        if ttl and ttl < datetime.now(UTC):
            await self.delete(key)
            return None

        # Get from cache
        res = self._cache.get(sha_key, None)
        if not res:
            return None

        # Move to first
        self._cache.move_to_end(sha_key, last=False)
        self._ttl.move_to_end(sha_key, last=False)

        return res

    async def set(
        self,
        key: str,
        ttl_sec: int,
        value: str | bytes | None,
    ) -> bool:
        """
        Set a value in the cache.
        """
        sha_key = self._key_to_hash(key)

        # Delete the last if full
        if len(self._cache) >= self._config.max_size:
            self._ttl.popitem()
            self._cache.popitem()

        # Set TTL as first element
        self._ttl[sha_key] = datetime.now(UTC) + timedelta(seconds=ttl_sec)
        self._ttl.move_to_end(sha_key, last=False)

        # Add cache as first element
        self._cache[sha_key] = value.encode() if isinstance(value, str) else value
        self._cache.move_to_end(sha_key, last=False)

        return True

    async def delete(self, key: str) -> bool:
        """
        Delete a value from the cache.
        """
        sha_key = self._key_to_hash(key)

        # Delete keys
        with suppress(KeyError):
            self._ttl.pop(sha_key)
        with suppress(KeyError):
            self._cache.pop(sha_key)

        return True

    @staticmethod
    def _key_to_hash(key: str) -> str:
        """
        Transform the key into a hash.

        SHA-256 lower the collision probability. Plus, it reduce the key size, which is useful for memory usage.
        """
        return hashlib.sha256(key.encode(), usedforsecurity=False).hexdigest()
