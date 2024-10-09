from abc import ABC, abstractmethod

from app.helpers.monitoring import tracer
from app.models.readiness import ReadinessEnum
from app.models.training import TrainingModel
from app.persistence.icache import ICache


class ISearch(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    @tracer.start_as_current_span("search_areadiness")
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @tracer.start_as_current_span("search_training_asearch_all")
    async def training_asearch_all(
        self,
        lang: str,
        text: str,
        cache_only: bool = False,
    ) -> list[TrainingModel] | None:
        pass
