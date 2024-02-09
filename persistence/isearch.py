from abc import ABC, abstractmethod
from models.call import CallModel
from models.training import TrainingModel
from typing import List, Optional


class ISearch(ABC):
    @abstractmethod
    async def training_asearch_all(
        self, text: str, call: CallModel
    ) -> Optional[List[TrainingModel]]:
        pass
