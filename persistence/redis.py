from helpers.config_models.cache import RedisModel
from helpers.logging import logger
from models.readiness import ReadinessEnum
from opentelemetry.instrumentation.redis import RedisInstrumentor
from persistence.icache import ICache
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import BusyLoadingError, ConnectionError, RedisError
from typing import Optional, Union
from uuid import uuid4
import hashlib


# Instrument redis
RedisInstrumentor().instrument()

_retry = Retry(backoff=ExponentialBackoff(), retries=3)


class RedisCache(ICache):
    _client: Redis
    _config: RedisModel

    def __init__(self, config: RedisModel):
        logger.info(f"Using Redis cache {config.host}:{config.port}")
        self._config = config
        self._client = Redis(
            # Database location
            db=config.database,
            # Reliability
            health_check_interval=10,  # Check the health of the connection every 10 secs
            retry_on_error=[BusyLoadingError, ConnectionError],
            retry_on_timeout=True,
            retry=_retry,
            socket_connect_timeout=5,  # Give the system sufficient time to connect even under higher CPU conditions
            socket_timeout=1,  # Respond quickly or abort, this is a cache
            # Deployment
            host=config.host,
            port=config.port,
            ssl=config.ssl,
            # Authentication
            password=config.password.get_secret_value(),
        )  # Redis manage by itself a low level connection pool with asyncio, but be warning to not use a generator while consuming the connection, it will close it

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the Redis cache.

        This will validate the ACID properties of the database: Create, Read, Update, Delete.
        """
        test_name = str(uuid4())
        test_value = "test"
        try:
            # Test the item does not exist
            assert await self._client.get(test_name) is None
            # Create a new item
            await self._client.set(test_name, test_value)
            # Test the item is the same
            assert (await self._client.get(test_name)).decode() == test_value
            # Delete the item
            await self._client.delete(test_name)
            # Test the item does not exist
            assert await self._client.get(test_name) is None
            return ReadinessEnum.OK
        except AssertionError:
            logger.error("Readiness test failed", exc_info=True)
        except RedisError as e:
            logger.error("Error requesting Redis", exc_info=True)
        except Exception:
            logger.error("Unknown error while checking Redis readiness", exc_info=True)
        return ReadinessEnum.FAIL

    async def aget(self, key: str) -> Optional[bytes]:
        """
        Get a value from the cache.

        If the key does not exist or if the key exists but the value is empty, return `None`.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        res = None
        try:
            res = await self._client.get(sha_key)
        except RedisError as e:
            logger.error(f"Error getting value: {e}")
        return res

    async def aset(self, key: str, value: Union[str, bytes, None]) -> bool:
        """
        Set a value in the cache.

        If the value is `None`, set an empty string.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        try:
            await self._client.set(sha_key, value if value else "")
        except RedisError as e:
            logger.error(f"Error setting value: {e}")
            return False
        return True

    async def adel(self, key: str) -> bool:
        """
        Delete a value from the cache.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        sha_key = self._key_to_hash(key)
        try:
            await self._client.delete(sha_key)
        except RedisError as e:
            logger.error(f"Error deleting value: {e}")
            return False
        return True

    @staticmethod
    def _key_to_hash(key: str) -> bytes:
        """
        Transform the key into a hash.

        SHA-256 lower the collision probability. Plus, it reduce the key size, which is useful for memory usage.
        """
        return hashlib.sha256(key.encode(), usedforsecurity=False).digest()
