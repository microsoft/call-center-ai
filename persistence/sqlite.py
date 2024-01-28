from aiosqlite import connect as sqlite_connect, Connection as SQLiteConnection
from contextlib import asynccontextmanager
from fastapi.encoders import jsonable_encoder
from helpers.config import CONFIG
from helpers.config_models.database import SqliteModel
from helpers.logging import build_logger
from models.call import CallModel
from persistence.istore import IStore
from pydantic import ValidationError
from typing import AsyncGenerator, List, Optional
from uuid import UUID
import json
import os


_logger = build_logger(__name__)
SCHEMA_VERSION = "3"


class SqliteStore(IStore):
    _config: SqliteModel

    def __init__(self, config: SqliteModel):
        _logger.info(
            f"Using SQLite database at {config.path} with table {config.table}"
        )
        self._config = config

    async def call_aget(self, call_id: UUID) -> Optional[CallModel]:
        _logger.debug(f"Loading call {call_id}")
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT data FROM {self._config.table} WHERE id = ?",
                (str(call_id),),
            )
            row = await cursor.fetchone()
            if row:
                try:
                    return CallModel.model_validate_json(row[0])
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        return None

    async def call_aset(self, call: CallModel) -> bool:
        # TODO: Catch exceptions and return False if something goes wrong
        data = jsonable_encoder(call.model_dump(), exclude_none=True)
        _logger.debug(f"Saving call {call.call_id}: {data}")
        async with self._use_db() as db:
            await db.execute(
                f"INSERT OR REPLACE INTO {self._config.table} VALUES (?, ?)",
                (
                    str(call.call_id),  # id
                    json.dumps(data),  # data
                ),
            )
            await db.commit()
        return True

    async def call_asearch_one(self, phone_number: str) -> Optional[CallModel]:
        _logger.debug(f"Loading last call for {phone_number}")
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT data FROM {self._config.table} WHERE (JSON_EXTRACT(data, '$.phone_number') LIKE ? OR JSON_EXTRACT(data, '$.claim.policyholder_phone') LIKE ?) AND DATETIME(JSON_EXTRACT(data, '$.created_at')) < DATETIME('now', '{CONFIG.workflow.conversation_timeout_hour} hours') ORDER BY DATETIME(JSON_EXTRACT(data, '$.created_at')) DESC LIMIT 1",
                (
                    phone_number,  # data.phone_number
                    phone_number,  # data.claim.policyholder_phone
                ),
            )
            row = await cursor.fetchone()
            if row:
                try:
                    return CallModel.model_validate_json(row[0])
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        return None

    async def call_asearch_all(self, phone_number: str) -> Optional[List[CallModel]]:
        _logger.debug(f"Loading all {self._config.table} for {phone_number}")
        calls = []
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT data FROM {self._config.table} WHERE JSON_EXTRACT(data, '$.phone_number') LIKE ? OR JSON_EXTRACT(data, '$.claim.policyholder_phone') LIKE ? ORDER BY DATETIME(JSON_EXTRACT(data, '$.created_at')) DESC",
                (
                    phone_number,  # data.phone_number
                    phone_number,  # data.claim.policyholder_phone
                ),
            )
            rows = await cursor.fetchall()
            for row in rows:
                if not row:
                    continue
                try:
                    calls.append(CallModel.model_validate_json(row[0]))
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        return calls or None

    async def _init_db(self, db: SQLiteConnection):
        """
        Initialize the database.

        See: https://sqlite.org/cgi/src/doc/wal2/doc/wal2.md
        """
        _logger.info("First run, init database")
        # Optimize performance for concurrent writes
        await db.execute("PRAGMA journal_mode=WAL")
        # Create table
        await db.execute(
            f"CREATE TABLE IF NOT EXISTS {self._config.table} (id VARCHAR(36) PRIMARY KEY, data TEXT)"
        )
        # Create indexes
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS {self._config.table}_data_phone_number ON {self._config.table} (JSON_EXTRACT(data, '$.phone_number'))"
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS {self._config.table}_data_created_at ON {self._config.table} (DATETIME(JSON_EXTRACT(data, '$.created_at')))"
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS {self._config.table}_data_claim_policyholder_phone ON {self._config.table} (JSON_EXTRACT(data, '$.claim.policyholder_phone'))"
        )

        # Write changes to disk
        await db.commit()

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[SQLiteConnection, None]:
        # Create folder
        db_path = self._config.full_path()
        first_run = False
        if not os.path.isfile(db_path):
            db_folder = db_path[: db_path.rfind("/")]
            os.makedirs(name=db_folder, exist_ok=True)
            first_run = True

        # Connect to DB
        async with sqlite_connect(database=db_path) as db:
            if first_run:
                await self._init_db(db)
            yield db
