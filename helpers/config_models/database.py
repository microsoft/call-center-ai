from enum import Enum
from pydantic import validator
from pydantic_settings import BaseSettings
from typing import Optional


class Mode(str, Enum):
    COSMOS_DB = "cosmos_db"
    SQLITE = "sqlite"


class CosmosDbModel(BaseSettings, env_prefix="database_cosmos_db_"):
    container: str
    database: str
    endpoint: str


class SqliteModel(BaseSettings, env_prefix="database_sqlite_"):
    path: str = ".local.sqlite"
    table: str = "calls"


class DatabaseModel(BaseSettings, env_prefix="database_"):
    cosmos_db: Optional[CosmosDbModel] = None
    mode: Mode = Mode.SQLITE
    sqlite: Optional[SqliteModel] = None

    @validator("cosmos_db", always=True)
    def check_cosmos_db(cls, v, values, **kwargs):
        if not v and values.get("mode", None) == Mode.COSMOS_DB:
            raise ValueError("Cosmos DB config required")
        return v

    @validator("sqlite", always=True)
    def check_sqlite(cls, v, values, **kwargs):
        if not v and values.get("mode", None) == Mode.SQLITE:
            raise ValueError("Sqlite config required")
        return v
