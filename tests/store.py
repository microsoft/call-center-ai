import pytest
from pytest_assume.plugin import assume

from app.helpers.config import CONFIG
from app.models.call import CallStateModel


@pytest.mark.asyncio(scope="session")
@pytest.mark.repeat(10)  # Catch multi-threading and concurrency issues
async def test_acid(call: CallStateModel) -> None:
    """
    Test ACID properties of the database backend.

    Steps:
    1. Create a mock data
    2. Test not exists
    3. Insert test data
    4. Check it exists

    Test is repeated 10 times to catch multi-threading and concurrency issues.
    """
    db = CONFIG.database.instance()

    # Check not exists
    assume(not await db.call_get(call.call_id))
    assume(await db.call_search_one(call.initiate.phone_number) != call)
    assume(
        call
        not in (
            (
                await db.call_search_all(
                    phone_number=call.initiate.phone_number, count=1
                )
            )[0]
            or []
        )
    )

    # Insert test call
    await db.call_create(call)

    # Check point read
    assume(await db.call_get(call.call_id) == call)
    # Check search one
    assume(await db.call_search_one(call.initiate.phone_number) == call)
    # Check search all
    assume(
        call
        in (
            (
                await db.call_search_all(
                    phone_number=call.initiate.phone_number, count=1
                )
            )[0]
            or []
        )
    )
