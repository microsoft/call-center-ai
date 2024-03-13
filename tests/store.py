from helpers.config import CONFIG
from helpers.config_models.database import ModeEnum as DatabaseModeEnum
from models.call import CallModel
import pytest


_db = CONFIG.database.instance()


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
@pytest.mark.asyncio
async def test_acid(call_mock: CallModel, database_mode: DatabaseModeEnum) -> None:
    # Set database mode
    CONFIG.database.mode = database_mode

    # Check not exists
    assert not await _db.call_aget(call_mock.call_id)
    assert not await _db.call_asearch_one(call_mock.phone_number)
    assert call_mock not in (await _db.call_asearch_all(call_mock.phone_number) or [])

    # Insert test call
    await _db.call_aset(call_mock)

    # Check point read
    assert await _db.call_aget(call_mock.call_id) == call_mock
    # Check search one
    assert await _db.call_asearch_one(call_mock.phone_number) == call_mock
    # Check search all
    assert call_mock in (await _db.call_asearch_all(call_mock.phone_number) or [])
