from abc import ABC, abstractmethod
from models.call import CallModel
from typing import List, Optional
from uuid import UUID


class IStore(ABC):
    @abstractmethod
    async def call_aget(self, call_id: UUID) -> Optional[CallModel]:
        pass

    @abstractmethod
    async def call_aset(self, call: CallModel) -> bool:
        pass

    @abstractmethod
    async def call_asearch_one(self, phone_number: str) -> Optional[CallModel]:
        pass

    @abstractmethod
    async def call_asearch_all(self, phone_number: str) -> Optional[List[CallModel]]:
        pass
