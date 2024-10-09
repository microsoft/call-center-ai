from functools import cache

from pydantic import BaseModel, Field

from app.persistence.isearch import ISearch


class AiSearchModel(BaseModel, frozen=True):
    endpoint: str
    expansion_n_messages: int = Field(default=10, ge=1)
    index: str
    semantic_configuration: str = "default"
    strictness: float = Field(default=2, ge=0, le=5)
    top_n_documents: int = Field(default=5, ge=1)

    @cache
    def instance(self) -> ISearch:
        from app.helpers.config import CONFIG
        from app.persistence.ai_search import (
            AiSearchSearch,
        )

        return AiSearchSearch(CONFIG.cache.instance(), self)
