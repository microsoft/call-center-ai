from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr

from persistence.isearch import ISearch


class AiSearchModel(BaseModel, frozen=True):
    access_key: SecretStr
    endpoint: str
    expansion_n_messages: int = Field(default=10, ge=1)
    index: str
    semantic_configuration: str = "default"
    strictness: float = Field(default=2, ge=0, le=5)
    top_n_documents: int = Field(default=5, ge=1)

    @lru_cache(maxsize=None)  # pylint: disable=method-cache-max-size-none
    def instance(self) -> ISearch:
        from helpers.config import CONFIG  # pylint: disable=import-outside-toplevel
        from persistence.ai_search import (  # pylint: disable=import-outside-toplevel
            AiSearchSearch,
        )

        return AiSearchSearch(CONFIG.cache.instance(), self)
