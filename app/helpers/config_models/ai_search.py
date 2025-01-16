from functools import cached_property

from pydantic import BaseModel, Field

from app.persistence.isearch import ISearch


class AiSearchModel(BaseModel, frozen=True):
    embedding_deployment: str
    embedding_dimensions: int
    embedding_endpoint: str
    embedding_model: str
    endpoint: str
    expansion_n_messages: int = Field(default=10, ge=1)
    index: str
    semantic_configuration: str = "semantic-default"
    strictness: float = Field(default=2, ge=0, le=5)
    top_n_documents: int = Field(default=5, ge=1)

    @cached_property
    def instance(self) -> ISearch:
        from app.helpers.config import CONFIG
        from app.persistence.ai_search import (
            AiSearchSearch,
        )

        return AiSearchSearch(
            cache=CONFIG.cache.instance,
            config=self,
        )
