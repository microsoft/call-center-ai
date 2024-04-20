from contextlib import asynccontextmanager
from helpers.config_models.cache import RedisModel
from helpers.logging import build_logger
from opentelemetry.instrumentation.redis import RedisInstrumentor
from persistence.icache import ICache
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError, RedisError
from typing import AsyncGenerator, Optional, Union
import hashlib


# Instrument redis
RedisInstrumentor().instrument()

_logger = build_logger(__name__)
_retry = Retry(ExponentialBackoff(), 3)


class RedisCache(ICache):
    _config: RedisModel

    def __init__(self, config: RedisModel):
        _logger.info(f"Using Redis cache {config.host}:{config.port}")
        self._config = config

    async def aget(self, key: str) -> Optional[bytes]:
        """
        Get a value from the cache.

        If the key does not exist or if the key exists but the value is empty, return `None`.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        res = None
        try:
            async with self._use_db() as db:
                res = await db.get(sha_key)
        except RedisError as e:
            _logger.error(f"Error getting value, {e}")
        return res

    async def aset(self, key: str, value: Union[str, bytes, None]) -> bool:
        """
        Set a value in the cache.

        If the value is `None`, set an empty string.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        try:
            async with self._use_db() as db:
                await db.set(sha_key, value if value else "")
        except RedisError as e:
            _logger.error(f"Error setting value, {e}")
            return False
        return True

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[Redis, None]:
        """
        Generate the Redis client and close it after use.
        """
        client = Redis(
            # Database location
            db=self._config.database,
            # Reliability
            retry_on_error=[BusyLoadingError, ConnectionError, TimeoutError],
            retry=_retry,
            socket_connect_timeout=10,
            # Azure deployment
            host=self._config.host,
            port=self._config.port,
            ssl=self._config.ssl,
            # Authentication with password
            password=self._config.password.get_secret_value(),
        )
        try:
            yield client
        finally:
            await client.aclose()

    @staticmethod
    def _key_to_hash(key: str) -> bytes:
        """
        Transform the key into a hash.

        SHA-256 lower the collision probability. Plus, it reduce the key size, which is useful for memory usage.
        """
        return hashlib.sha256(key.encode(), usedforsecurity=False).digest()
