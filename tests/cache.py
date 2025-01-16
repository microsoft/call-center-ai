import pytest
from pytest_assume.plugin import assume

from app.helpers.config import CONFIG
from app.helpers.config_models.cache import ModeEnum as CacheModeEnum


@pytest.mark.parametrize(
    "cache_mode",
    [
        pytest.param(
            CacheModeEnum.MEMORY,
            id="memory",
        ),
        pytest.param(
            CacheModeEnum.REDIS,
            id="redis",
        ),
    ],
)
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.repeat(10)  # Catch multi-threading and concurrency issues
async def test_acid(
    cache_mode: CacheModeEnum,
    random_text: str,
) -> None:
    """
    Test ACID properties of the cache backend.

    Steps:
    1. Create a mock data
    2. Test not exists
    3. Insert test data
    4. Check it exists

    Test is repeated 10 times to catch multi-threading and concurrency issues.
    """
    # Set cache mode
    CONFIG.cache.mode = cache_mode
    cache = CONFIG.cache.instance

    # Init values
    test_key = random_text
    test_value = "lorem ipsum"

    # Check not exists
    assume(not await cache.get(test_key))

    # Insert test call
    await cache.set(
        key=test_key,
        ttl_sec=60,
        value=test_value,
    )

    # Check point read
    assume(await cache.get(test_key) == test_value.encode())
