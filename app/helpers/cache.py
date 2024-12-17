import asyncio
import functools
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager

from aiojobs import Scheduler


@asynccontextmanager
async def get_scheduler() -> AsyncGenerator[Scheduler, None]:
    """
    Get the scheduler for async background tasks.

    Scheduler should be used for each request to avoid conflicts. It is closed automatically, waiting 45 secs for tasks to finish.
    """
    async with Scheduler(
        close_timeout=45,  # Default request timeout is 60 secs, should be enough
    ) as scheduler:
        yield scheduler


def async_lru_cache(maxsize: int = 128):
    """
    Caches a function's return value each time it is called.

    If the maxsize is reached, the least recently used value is removed.
    """

    def decorator(func):
        cache: OrderedDict[tuple, Awaitable] = OrderedDict()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Awaitable:
            # Create a cache key from event loop, args and kwargs, using frozenset for kwargs to ensure hashability
            key = (
                id(asyncio.get_event_loop()),
                args,
                frozenset(kwargs.items()),
            )

            if key in cache:
                # Move the recently accessed key to the end (most recently used)
                cache.move_to_end(key)
                return cache[key]

            # Compute the value since it's not cached
            value = await func(*args, **kwargs)
            cache[key] = value
            cache.move_to_end(key)

            if len(cache) > maxsize:
                cache.popitem(last=False)

            return value

        return wrapper

    return decorator
