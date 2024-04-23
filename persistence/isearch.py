from abc import ABC, abstractmethod
from models.call import CallModel
from models.readiness import ReadinessStatus
from models.training import TrainingModel
from persistence.icache import ICache
from typing import Optional


class ISearch(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    async def areadiness(self) -> ReadinessStatus:
        pass

    @abstractmethod
    async def training_asearch_all(
        self, text: str, call: CallModel
    ) -> Optional[list[TrainingModel]]:
        pass
