from helpers.config import CONFIG
from helpers.config_models.database import ModeEnum as DatabaseModeEnum
from models.call import CallStateModel
from pytest import assume
import pytest


@pytest.mark.parametrize(
    "database_mode",
    [
        pytest.param(
            DatabaseModeEnum.SQLITE,
            id="sqlite",
        ),
        pytest.param(
            DatabaseModeEnum.COSMOS_DB,
            id="cosmos_db",
        ),
    ],
)
@pytest.mark.asyncio(scope="session")
@pytest.mark.repeat(10)  # Catch multi-threading and concurrency issues
async def test_acid(call: CallStateModel, database_mode: DatabaseModeEnum) -> None:
    """
    Test ACID properties of the database backend.

    Steps:
    1. Create a mock data
    2. Test not exists
    3. Insert test data
    4. Check it exists

    Test is repeated 10 times to catch multi-threading and concurrency issues.
    """
    # Set database mode
    CONFIG.database.mode = database_mode
    db = CONFIG.database.instance()

    # Check not exists
    assume(not await db.call_aget(call.call_id))
    assume(await db.call_asearch_one(call.initiate.phone_number) != call)
    assume(
        call
        not in (
            (
                await db.call_asearch_all(
                    phone_number=call.initiate.phone_number, count=1
                )
            )[0]
            or []
        )
    )

    # Insert test call
    await db.call_aset(call)

    # Check point read
    assume(await db.call_aget(call.call_id) == call)
    # Check search one
    assume(await db.call_asearch_one(call.initiate.phone_number) == call)
    # Check search all
    assume(
        call
        in (
            (
                await db.call_asearch_all(
                    phone_number=call.initiate.phone_number, count=1
                )
            )[0]
            or []
        )
    )
