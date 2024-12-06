from functools import cache

from pydantic import BaseModel

from app.persistence.istore import IStore


class CosmosDbModel(BaseModel, frozen=True):
    container: str
    database: str
    endpoint: str

    @cache
    def instance(self) -> IStore:
        from app.helpers.config import CONFIG
        from app.persistence.cosmos_db import (
            CosmosDbStore,
        )

        return CosmosDbStore(CONFIG.cache.instance(), self)


class DatabaseModel(BaseModel):
    cosmos_db: CosmosDbModel

    def instance(self) -> IStore:
        assert self.cosmos_db
        return self.cosmos_db.instance()
