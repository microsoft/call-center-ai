from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, SecretStr, ValidationInfo, field_validator

from persistence.istore import IStore


class ModeEnum(str, Enum):
    COSMOS_DB = "cosmos_db"
    SQLITE = "sqlite"


class CosmosDbModel(BaseModel, frozen=True):
    access_key: SecretStr
    container: str
    database: str
    endpoint: str

    @lru_cache(maxsize=None)  # pylint: disable=method-cache-max-size-none
    def instance(self) -> IStore:
        from helpers.config import CONFIG  # pylint: disable=import-outside-toplevel
        from persistence.cosmos_db import (  # pylint: disable=import-outside-toplevel
            CosmosDbStore,
        )

        return CosmosDbStore(CONFIG.cache.instance(), self)


class SqliteModel(BaseModel, frozen=True):
    path: str = ".local"
    schema_version: int = 3
    table: str = "calls"

    def full_path(self) -> str:
        """
        Returns the full path to the sqlite database file.

        Formatted as: `{path}-v{schema_version}.sqlite`.
        """
        return f"{self.path}-v{self.schema_version}.sqlite"

    @lru_cache(maxsize=None)  # pylint: disable=method-cache-max-size-none
    def instance(self) -> IStore:
        from helpers.config import CONFIG  # pylint: disable=import-outside-toplevel
        from persistence.sqlite import (  # pylint: disable=import-outside-toplevel
            SqliteStore,
        )

        return SqliteStore(CONFIG.cache.instance(), self)


class DatabaseModel(BaseModel):
    cosmos_db: Optional[CosmosDbModel] = None
    mode: ModeEnum = ModeEnum.SQLITE
    sqlite: Optional[SqliteModel] = SqliteModel()  # Object is fully defined by default

    @field_validator("cosmos_db")
    @classmethod
    def _validate_cosmos_db(
        cls,
        cosmos_db: Optional[CosmosDbModel],
        info: ValidationInfo,
    ) -> Optional[CosmosDbModel]:
        if not cosmos_db and info.data.get("mode", None) == ModeEnum.COSMOS_DB:
            raise ValueError("Cosmos DB config required")
        return cosmos_db

    @field_validator("sqlite")
    @classmethod
    def _validate_sqlite(
        cls,
        sqlite: Optional[SqliteModel],
        info: ValidationInfo,
    ) -> Optional[SqliteModel]:
        if not sqlite and info.data.get("mode", None) == ModeEnum.SQLITE:
            raise ValueError("SQLite config required")
        return sqlite

    def instance(self) -> IStore:
        if self.mode == ModeEnum.SQLITE:
            assert self.sqlite
            return self.sqlite.instance()

        assert self.cosmos_db
        return self.cosmos_db.instance()
