from aiosqlite import connect as sqlite_connect, Connection as SQLiteConnection
from contextlib import asynccontextmanager
from helpers.config import CONFIG
from helpers.config_models.database import SqliteModel
from helpers.logging import build_logger
from models.call import CallStateModel
from models.readiness import ReadinessStatus
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from persistence.istore import IStore
from pydantic import ValidationError
from typing import AsyncGenerator, Optional
from uuid import UUID
import asyncio
import os


# Instrument sqlite
SQLite3Instrumentor().instrument()

_logger = build_logger(__name__)


class SqliteStore(IStore):
    _config: SqliteModel

    def __init__(self, config: SqliteModel):
        _logger.info(
            f"Using SQLite database at {config.path} with table {config.table}"
        )
        self._config = config

    async def areadiness(self) -> ReadinessStatus:
        """
        Check the readiness of the SQLite database.

        This checks if the database is reachable and can be queried.
        """
        try:
            async with self._use_db() as db:
                await db.execute("SELECT 1")
            return ReadinessStatus.OK
        except Exception as e:
            _logger.error(f"Error requesting SQLite, {e}")
        return ReadinessStatus.FAIL

    async def call_aget(self, call_id: UUID) -> Optional[CallStateModel]:
        _logger.debug(f"Loading call {call_id}")
        call = None
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT data FROM {self._config.table} WHERE id = ?",
                (str(call_id),),
            )
            row = await cursor.fetchone()
            if row:
                try:
                    call = CallStateModel.model_validate_json(row[0])
                except ValidationError as e:
                    _logger.debug(f"Parsing error: {e.errors()}")
        return call

    async def call_aset(self, call: CallStateModel) -> bool:
        # TODO: Catch exceptions and return False if something goes wrong
        data = call.model_dump_json(exclude_none=True)
        _logger.debug(f"Saving call {call.call_id}: {data}")
        async with self._use_db() as db:
            await db.execute(
                f"INSERT OR REPLACE INTO {self._config.table} VALUES (?, ?)",
                (
                    str(call.call_id),  # id
                    data,  # data
                ),
            )
            await db.commit()
        return True

    async def call_asearch_one(self, phone_number: str) -> Optional[CallStateModel]:
        _logger.debug(f"Loading last call for {phone_number}")
        call = None
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT data FROM {self._config.table} WHERE (JSON_EXTRACT(data, '$.initiate.phone_number') LIKE ? OR JSON_EXTRACT(data, '$.claim.policyholder_phone') LIKE ?) AND DATETIME(JSON_EXTRACT(data, '$.created_at')) >= DATETIME('now', '-{CONFIG.workflow.conversation_timeout_hour} hours') ORDER BY DATETIME(JSON_EXTRACT(data, '$.created_at')) DESC LIMIT 1",
                (
                    phone_number,  # data.initiate.phone_number
                    phone_number,  # data.claim.policyholder_phone
                ),
            )
            row = await cursor.fetchone()
            if row:
                try:
                    call = CallStateModel.model_validate_json(row[0])
                except ValidationError as e:
                    _logger.debug(f"Parsing error: {e.errors()}")
        return call

    async def call_asearch_all(
        self,
        count: int,
        phone_number: Optional[str] = None,
    ) -> tuple[Optional[list[CallStateModel]], int]:
        _logger.debug(f"Searching calls, for {phone_number} and count {count}")
        calls, total = await asyncio.gather(
            self._call_asearch_all_calls_worker(count, phone_number),
            self._call_asearch_all_total_worker(phone_number),
        )
        return calls, total

    async def _call_asearch_all_calls_worker(
        self,
        count: int,
        phone_number: Optional[str] = None,
    ) -> Optional[list[CallStateModel]]:
        calls: list[CallStateModel] = []
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT data FROM {self._config.table} {"WHERE (JSON_EXTRACT(data, '$.initiate.phone_number') LIKE ? OR JSON_EXTRACT(data, '$.claim.policyholder_phone') LIKE ?)" if phone_number else ""} ORDER BY DATETIME(JSON_EXTRACT(data, '$.created_at')) DESC LIMIT ?",
                (
                    (
                        phone_number,  # data.initiate.phone_number
                        phone_number,  # data.claim.policyholder_phone
                        count,  # limit
                    )
                    if phone_number
                    else (count,)  # limit
                ),
            )
            rows = await cursor.fetchall()
            for row in rows:
                if not row:
                    continue
                try:
                    calls.append(CallStateModel.model_validate_json(row[0]))
                except ValidationError as e:
                    _logger.debug(f"Parsing error: {e.errors()}")
        return calls

    async def _call_asearch_all_total_worker(
        self,
        phone_number: Optional[str] = None,
    ) -> int:
        async with self._use_db() as db:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM {self._config.table} {"WHERE (JSON_EXTRACT(data, '$.initiate.phone_number') LIKE ? OR JSON_EXTRACT(data, '$.claim.policyholder_phone') LIKE ?)" if phone_number else ""}",
                (
                    (
                        phone_number,  # data.initiate.phone_number
                        phone_number,  # data.claim.policyholder_phone
                    )
                    if phone_number
                    else ()
                ),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

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
            f"CREATE INDEX IF NOT EXISTS {self._config.table}_data_initiate_phone_number ON {self._config.table} (JSON_EXTRACT(data, '$.initiate.phone_number'))"
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
        """
        Generate the SQLite client and close it after use.
        """
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
