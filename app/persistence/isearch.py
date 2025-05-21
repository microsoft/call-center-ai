from abc import ABC, abstractmethod

from app.helpers.monitoring import start_as_current_span
from app.models.readiness import ReadinessEnum
from app.models.training import TrainingModel
from app.persistence.icache import ICache


class ISearch(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    @start_as_current_span("search_readiness")
    async def readiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @start_as_current_span("search_training_search_all")
    async def training_search_all(
        self,
        lang: str,
        text: str,
        cache_only: bool = False,
    ) -> list[TrainingModel] | None:
        pass
