from abc import ABC, abstractmethod
from typing import Optional

from models.readiness import ReadinessEnum
from models.training import TrainingModel
from persistence.icache import ICache


class ISearch(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    async def training_asearch_all(
        self,
        lang: str,
        text: str,
        cache_only: bool = False,
    ) -> Optional[list[TrainingModel]]:
        pass
