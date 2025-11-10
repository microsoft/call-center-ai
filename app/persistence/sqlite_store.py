"""SQLite implementation of IStore for local development."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from aiojobs import Scheduler
from pydantic import ValidationError

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore

from app.helpers.config_models.database import SqliteModel
from app.helpers.features import callback_timeout_hour
from app.helpers.logging import logger
from app.models.call import CallStateModel
from app.models.readiness import ReadinessEnum
from app.persistence.icache import ICache
from app.persistence.istore import IStore


class SqliteStore(IStore):
    """
    SQLite implementation of IStore.

    Provides a lightweight, file-based storage option for local development
    and small deployments. Much easier to run than Cosmos DB!

    Features:
    - Single file database (no server needed)
    - ACID transactions
    - Great for development, testing, POCs
    - Suitable for low-to-medium traffic production use

    SQLite in Python 3.13+ has excellent async support via aiosqlite.
    """

    _config: SqliteModel
    _db_path: str
    _initialized: bool = False

    def __init__(self, cache: ICache, config: SqliteModel):
        super().__init__(cache)
        self._config = config
        self._db_path = config.database_path
        logger.info("Using SQLite database at %s", self._db_path)

    async def _ensure_initialized(self) -> None:
        """Initialize database schema if not already done."""
        if self._initialized:
            return

        if aiosqlite is None:
            raise ImportError(
                "aiosqlite is required for SQLite support. "
                "Install it with: pip install aiosqlite"
            )

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY,
                    phone_number TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data TEXT NOT NULL
                )
                """
            )
            # Index for phone number lookups
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_phone_number
                ON calls(phone_number, created_at DESC)
                """
            )
            await db.commit()

        self._initialized = True
        logger.debug("SQLite database initialized")

    async def readiness(self) -> ReadinessEnum:
        """Check if SQLite is ready."""
        try:
            await self._ensure_initialized()

            # Test basic operations
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute("SELECT 1")
                result = await cursor.fetchone()
                if result and result[0] == 1:
                    return ReadinessEnum.OK

            return ReadinessEnum.FAIL

        except Exception:
            logger.exception("SQLite readiness check failed")
            return ReadinessEnum.FAIL

    async def call_get(
        self,
        call_id: UUID,
    ) -> CallStateModel | None:
        """Get a call by ID."""
        logger.debug("Loading call %s", call_id)

        # Try cache first
        cache_key = self._cache_key_call_id(call_id)
        cached = await self._cache.get(cache_key)
        if cached:
            try:
                return CallStateModel.model_validate_json(cached)
            except ValidationError as e:
                logger.debug("Cache parsing error: %s", e.errors())

        # Query database
        await self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT data FROM calls WHERE id = ?",
                    (str(call_id),),
                )
                row = await cursor.fetchone()

                if row:
                    try:
                        call = CallStateModel.model_validate_json(row[0])

                        # Update cache
                        await self._cache.set(
                            key=cache_key,
                            ttl_sec=max(await callback_timeout_hour(), 1) * 60 * 60,
                            value=call.model_dump_json(),
                        )

                        return call
                    except ValidationError as e:
                        logger.error("Error parsing call data: %s", e.errors())

        except Exception:
            logger.exception("Error loading call %s from SQLite", call_id)

        return None

    @asynccontextmanager
    async def call_transac(
        self,
        call: CallStateModel,
        scheduler: Scheduler,
    ) -> AsyncGenerator[None]:
        """Transaction context manager for call updates."""
        init_data = call.model_dump(mode="json", exclude_none=True)
        yield

        async def _exec() -> None:
            # Compute the diff
            current_data = call.model_dump(mode="json", exclude_none=True)
            has_changes = init_data != current_data

            if not has_changes:
                logger.debug("No update needed for call %s", call.call_id)
                return

            # Save to database
            await self._ensure_initialized()

            try:
                async with aiosqlite.connect(self._db_path) as db:
                    call_json = call.model_dump_json()

                    await db.execute(
                        """
                        INSERT INTO calls (id, phone_number, data, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(id) DO UPDATE SET
                            data = excluded.data,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            str(call.call_id),
                            call.initiate.phone_number,
                            call_json,
                        ),
                    )
                    await db.commit()

                    logger.debug("Updated call %s in SQLite", call.call_id)

                    # Update cache
                    cache_key = self._cache_key_call_id(call.call_id)
                    await self._cache.set(
                        key=cache_key,
                        ttl_sec=max(await callback_timeout_hour(), 1) * 60 * 60,
                        value=call_json,
                    )

            except Exception:
                logger.exception("Error updating call %s in SQLite", call.call_id)

        await scheduler.spawn(_exec())

    async def call_create(
        self,
        call: CallStateModel,
    ) -> CallStateModel:
        """Create a new call."""
        await self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                call_json = call.model_dump_json()

                await db.execute(
                    """
                    INSERT INTO calls (id, phone_number, data)
                    VALUES (?, ?, ?)
                    """,
                    (
                        str(call.call_id),
                        call.initiate.phone_number,
                        call_json,
                    ),
                )
                await db.commit()

                logger.info("Created call %s in SQLite", call.call_id)

                # Update cache
                cache_key = self._cache_key_call_id(call.call_id)
                await self._cache.set(
                    key=cache_key,
                    ttl_sec=max(await callback_timeout_hour(), 1) * 60 * 60,
                    value=call_json,
                )

                return call

        except Exception:
            logger.exception("Error creating call %s in SQLite", call.call_id)
            raise

    async def call_search_one(
        self,
        phone_number: str,
        callback_timeout: bool = True,
    ) -> CallStateModel | None:
        """Search for the most recent call by phone number."""
        logger.debug("Searching for call with phone number %s", phone_number)

        # Try cache first
        cache_key = self._cache_key_phone_number(phone_number)
        cached = await self._cache.get(cache_key)
        if cached:
            try:
                return CallStateModel.model_validate_json(cached)
            except ValidationError as e:
                logger.debug("Cache parsing error: %s", e.errors())

        # Query database
        await self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                # Get most recent call for this phone number
                cursor = await db.execute(
                    """
                    SELECT data FROM calls
                    WHERE phone_number = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (phone_number,),
                )
                row = await cursor.fetchone()

                if row:
                    try:
                        call = CallStateModel.model_validate_json(row[0])

                        # Update cache
                        await self._cache.set(
                            key=cache_key,
                            ttl_sec=max(await callback_timeout_hour(), 1) * 60 * 60,
                            value=call.model_dump_json(),
                        )

                        return call
                    except ValidationError as e:
                        logger.error("Error parsing call data: %s", e.errors())

        except Exception:
            logger.exception("Error searching for call with phone %s", phone_number)

        return None

    async def call_search_all(
        self,
        count: int,
        phone_number: str | None = None,
    ) -> tuple[list[CallStateModel] | None, int]:
        """Search for all calls, optionally filtered by phone number."""
        await self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                # Count total
                if phone_number:
                    count_cursor = await db.execute(
                        "SELECT COUNT(*) FROM calls WHERE phone_number = ?",
                        (phone_number,),
                    )
                else:
                    count_cursor = await db.execute("SELECT COUNT(*) FROM calls")

                total_row = await count_cursor.fetchone()
                total = total_row[0] if total_row else 0

                # Get calls
                if phone_number:
                    cursor = await db.execute(
                        """
                        SELECT data FROM calls
                        WHERE phone_number = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (phone_number, count),
                    )
                else:
                    cursor = await db.execute(
                        """
                        SELECT data FROM calls
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (count,),
                    )

                rows = await cursor.fetchall()

                if not rows:
                    return None, total

                calls = []
                for row in rows:
                    try:
                        call = CallStateModel.model_validate_json(row[0])
                        calls.append(call)
                    except ValidationError as e:
                        logger.error("Error parsing call data: %s", e.errors())

                return calls if calls else None, total

        except Exception:
            logger.exception("Error searching calls")
            return None, 0
