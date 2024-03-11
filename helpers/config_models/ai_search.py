from functools import cache
from persistence.isearch import ISearch
from pydantic import SecretStr, BaseModel, Field


class AiSearchModel(BaseModel, frozen=True):
    access_key: SecretStr
    endpoint: str
    expansion_k: int = Field(default=5, ge=1)
    index: str
    semantic_configuration: str
    top_k: int = Field(default=15, ge=1)

    @cache
    def instance(self) -> ISearch:
        from helpers.config import CONFIG
        from persistence.ai_search import AiSearchSearch

        return AiSearchSearch(CONFIG.cache.instance(), self)
