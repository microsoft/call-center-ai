from enum import Enum
from functools import cached_property

from pydantic import BaseModel, ValidationInfo, field_validator

from app.persistence.istore import IStore


class ModeEnum(str, Enum):
    COSMOS_DB = "cosmos_db"
    """Use Azure Cosmos DB."""
    SQLITE = "sqlite"
    """Use SQLite (local file-based database)."""


class CosmosDbModel(BaseModel, frozen=True):
    container: str
    database: str
    endpoint: str

    @cached_property
    def instance(self) -> IStore:
        from app.helpers.config import CONFIG
        from app.persistence.cosmos_db import (
            CosmosDbStore,
        )

        return CosmosDbStore(
            cache=CONFIG.cache.instance,
            config=self,
        )


class SqliteModel(BaseModel, frozen=True):
    database_path: str = "./data/calls.db"
    """Path to SQLite database file."""

    @cached_property
    def instance(self) -> IStore:
        from app.helpers.config import CONFIG
        from app.persistence.sqlite_store import (
            SqliteStore,
        )

        return SqliteStore(
            cache=CONFIG.cache.instance,
            config=self,
        )


class DatabaseModel(BaseModel):
    cosmos_db: CosmosDbModel | None = None
    mode: ModeEnum = ModeEnum.COSMOS_DB
    sqlite: SqliteModel | None = SqliteModel()

    @field_validator("cosmos_db")
    @classmethod
    def _validate_cosmos_db(
        cls,
        cosmos_db: CosmosDbModel | None,
        info: ValidationInfo,
    ) -> CosmosDbModel | None:
        if not cosmos_db and info.data.get("mode", None) == ModeEnum.COSMOS_DB:
            raise ValueError("Cosmos DB config required")
        return cosmos_db

    @field_validator("sqlite")
    @classmethod
    def _validate_sqlite(
        cls,
        sqlite: SqliteModel | None,
        info: ValidationInfo,
    ) -> SqliteModel | None:
        if not sqlite and info.data.get("mode", None) == ModeEnum.SQLITE:
            raise ValueError("SQLite config required")
        return sqlite

    @cached_property
    def instance(self) -> IStore:
        if self.mode == ModeEnum.COSMOS_DB:
            assert self.cosmos_db
            return self.cosmos_db.instance

        assert self.sqlite
        return self.sqlite.instance
