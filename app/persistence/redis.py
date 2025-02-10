import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from opentelemetry.instrumentation.redis import RedisInstrumentor
from redis.asyncio import Connection, ConnectionPool, Redis, SSLConnection
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import (
    BusyLoadingError,
    ConnectionError as RedisConnectionError,
    RedisError,
)

from app.helpers.cache import lru_acache
from app.helpers.config_models.cache import RedisModel
from app.helpers.logging import logger
from app.models.readiness import ReadinessEnum
from app.persistence.icache import ICache

# Instrument redis
RedisInstrumentor().instrument()


class RedisCache(ICache):
    _config: RedisModel

    def __init__(self, config: RedisModel):
        self._config = config

    async def readiness(self) -> ReadinessEnum:
        """
        Check the readiness of the Redis cache.

        This will validate the ACID properties of the database: Create, Read, Update, Delete.
        """
        test_name = str(uuid4())
        test_value = "test"
        try:
            async with self._use_client() as client:
                # Test the item does not exist
                assert await client.get(test_name) is None
                # Create a new item
                await client.set(test_name, test_value)
                # Test the item is the same
                assert (await client.get(test_name)).decode() == test_value
                # Delete the item
                await client.delete(test_name)
                # Test the item does not exist
                assert await client.get(test_name) is None
            return ReadinessEnum.OK
        except AssertionError:
            logger.exception("Readiness test failed")
        except RedisError:
            logger.exception("Error requesting Redis")
        except Exception:
            logger.exception("Unknown error while checking Redis readiness")
        return ReadinessEnum.FAIL

    async def get(self, key: str) -> bytes | None:
        """
        Get a value from the cache.

        If the key does not exist or if the key exists but the value is empty, return `None`.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        res = None
        try:
            async with self._use_client() as client:
                res = await client.get(sha_key)
        except RedisError:
            logger.exception("Error getting value")
        return res

    async def set(
        self,
        key: str,
        ttl_sec: int,
        value: str | bytes | None,
    ) -> bool:
        """
        Set a value in the cache.

        If the value is `None`, set an empty string.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        try:
            async with self._use_client() as client:
                await client.set(
                    ex=ttl_sec,
                    name=sha_key,
                    value=value if value else "",
                )
        except RedisError:
            logger.exception("Error setting value")
            return False
        return True

    async def delete(self, key: str) -> bool:
        """
        Delete a value from the cache.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        try:
            async with self._use_client() as client:
                await client.delete(sha_key)
        except RedisError:
            logger.exception("Error deleting value")
            return False
        return True

    @lru_acache()
    async def _use_connection_pool(self) -> ConnectionPool:
        """
        Generate the Redis connection pool.
        """
        logger.info("Using Redis cache %s:%s", self._config.host, self._config.port)

        return ConnectionPool(
            # Database location
            db=self._config.database,
            # Reliability
            health_check_interval=10,  # Check the health of the connection every 10 secs
            retry_on_error=[BusyLoadingError, RedisConnectionError],
            retry_on_timeout=True,
            retry=Retry(backoff=ExponentialBackoff(), retries=3),
            socket_connect_timeout=5,  # Give the system sufficient time to connect even under higher CPU conditions
            socket_timeout=1,  # Respond quickly or abort, this is a cache
            # Deployment
            connection_class=SSLConnection if self._config.ssl else Connection,
            host=self._config.host,
            port=self._config.port,
            # Authentication
            password=self._config.password.get_secret_value()
            if self._config.password
            else None,
        )

    @asynccontextmanager
    async def _use_client(self) -> AsyncGenerator[Redis]:
        """
        Return a Redis connection.
        """
        async with Redis(
            auto_close_connection_pool=False,
            connection_pool=await self._use_connection_pool(),
        ) as client:
            yield client

    @staticmethod
    def _key_to_hash(key: str) -> bytes:
        """
        Transform the key into a hash.

        SHA-256 lower the collision probability. Plus, it reduce the key size, which is useful for memory usage.
        """
        return hashlib.sha256(key.encode(), usedforsecurity=False).digest()
