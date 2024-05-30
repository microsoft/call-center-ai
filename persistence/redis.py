from contextlib import asynccontextmanager
from helpers.config_models.cache import RedisModel
from helpers.logging import logger
from models.readiness import ReadinessStatus
from opentelemetry.instrumentation.redis import RedisInstrumentor
from persistence.icache import ICache
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import BusyLoadingError, ConnectionError, RedisError
from typing import AsyncGenerator, Optional, Union
from uuid import uuid4
import hashlib


# Instrument redis
RedisInstrumentor().instrument()

_retry = Retry(backoff=ExponentialBackoff(), retries=3)


class RedisCache(ICache):
    _config: RedisModel

    def __init__(self, config: RedisModel):
        logger.info(f"Using Redis cache {config.host}:{config.port}")
        self._config = config

    async def areadiness(self) -> ReadinessStatus:
        """
        Check the readiness of the Redis cache.

        This will validate the ACID properties of the database: Create, Read, Update, Delete.
        """
        test_name = str(uuid4())
        test_value = "test"
        try:
            async with self._use_db() as db:
                # Test the item does not exist
                assert await db.get(test_name) is None
                # Create a new item
                await db.set(test_name, test_value)
                # Test the item is the same
                assert (await db.get(test_name)).decode() == test_value
                # Delete the item
                await db.delete(test_name)
                # Test the item does not exist
                assert await db.get(test_name) is None
            return ReadinessStatus.OK
        except AssertionError as e:
            logger.error(f"Readiness test failed, {e}")
        except RedisError as e:
            logger.error(f"Error requesting Redis, {e}")
        return ReadinessStatus.FAIL

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
            logger.error(f"Error getting value, {e}")
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
            logger.error(f"Error setting value, {e}")
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
            retry_on_error=[BusyLoadingError, ConnectionError],
            retry_on_timeout=True,
            retry=_retry,
            socket_connect_timeout=1,  # Timeout for connection, we want it to fail fast, that's cache
            socket_timeout=10,  # Timeout for queries
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
