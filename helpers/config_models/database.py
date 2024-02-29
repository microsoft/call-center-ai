from enum import Enum
from pydantic import validator, SecretStr, Field, BaseModel
from typing import Optional


class ModeEnum(str, Enum):
    COSMOS_DB = "cosmos_db"
    SQLITE = "sqlite"


class CosmosDbModel(BaseModel):
    access_key: SecretStr
    container: str
    database: str
    endpoint: str


class SqliteModel(BaseModel):
    path: str = ".local"
    schema_version: int = Field(default=3, frozen=True)
    table: str = "calls"

    def full_path(self) -> str:
        """
        Returns the full path to the sqlite database file.

        Formatted as: `{path}-v{schema_version}.sqlite`.
        """
        return f"{self.path}-v{self.schema_version}.sqlite"


class DatabaseModel(BaseModel):
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
