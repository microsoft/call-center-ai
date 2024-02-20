from contextlib import asynccontextmanager
from helpers.config_models.cache import RedisModel
from helpers.logging import build_logger
from persistence.icache import ICache
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError
from typing import AsyncGenerator, Optional, Union


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
        res = None
        async with self._use_db() as db:
            res = await db.get(key)
        return res

    async def aset(self, key: str, value: Union[str, bytes, None]) -> None:
        """
        Set a value in the cache.

        If the value is `None`, set an empty string.

        Catch errors for a maximum of 3 times, then raise the error.
        """
        async with self._use_db() as db:
            await db.set(key, value if value else "")

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[Redis, None]:
        client = Redis(
            # Database location
            db=self._config.database,
            # Reliability
            retry_on_error=[BusyLoadingError, ConnectionError, TimeoutError],
            retry=_retry,
            socket_connect_timeout=5,
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
