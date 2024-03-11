from enum import Enum
from functools import cache
from persistence.istore import IStore
from persistence.sqlite import SqliteStore
from pydantic import validator, SecretStr, Field, BaseModel
from typing import Optional


class ModeEnum(str, Enum):
    COSMOS_DB = "cosmos_db"
    SQLITE = "sqlite"


class CosmosDbModel(BaseModel, frozen=True):
    access_key: SecretStr
    container: str
    database: str
    endpoint: str


class SqliteModel(BaseModel, frozen=True):
    path: str = ".local"
    schema_version: int = Field(default=3, frozen=True)
    table: str = "calls"

    def full_path(self) -> str:
        """
        Returns the full path to the sqlite database file.

        Formatted as: `{path}-v{schema_version}.sqlite`.
        """
        return f"{self.path}-v{self.schema_version}.sqlite"


class DatabaseModel(BaseModel, frozen=True):
    cosmos_db: Optional[CosmosDbModel] = None
    mode: ModeEnum = ModeEnum.SQLITE
    sqlite: Optional[SqliteModel] = None

    @validator("cosmos_db", always=True)
    def validate_cosmos_db(
        cls, v: Optional[CosmosDbModel], values, **kwargs
    ) -> Optional[CosmosDbModel]:
        if not v and values.get("mode", None) == ModeEnum.COSMOS_DB:
            raise ValueError("Cosmos DB config required")
        return v

    @validator("sqlite", always=True)
    def validate_sqlite(
        cls, v: Optional[SqliteModel], values, **kwargs
    ) -> Optional[SqliteModel]:
        if not v and values.get("mode", None) == ModeEnum.SQLITE:
            raise ValueError("SQLite config required")
        return v

    @cache
    def instance(self) -> IStore:
        if self.mode == ModeEnum.SQLITE:
            from persistence.sqlite import SqliteStore

            assert self.sqlite
            return SqliteStore(self.sqlite)

        from persistence.cosmos_db import CosmosDbStore

        assert self.cosmos_db
        return CosmosDbStore(self.cosmos_db)
