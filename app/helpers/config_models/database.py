from functools import lru_cache

from pydantic import BaseModel

from app.persistence.istore import IStore


class CosmosDbModel(BaseModel, frozen=True):
    container: str
    database: str
    endpoint: str

    @lru_cache
    def instance(self) -> IStore:
        from app.helpers.config import CONFIG
        from app.persistence.cosmos_db import (
            CosmosDbStore,
        )

        return CosmosDbStore(
            cache=CONFIG.cache.instance(),
            config=self,
        )


class DatabaseModel(BaseModel):
    cosmos_db: CosmosDbModel

    def instance(self) -> IStore:
        return self.cosmos_db.instance()
