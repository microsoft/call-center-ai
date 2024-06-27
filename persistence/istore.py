from abc import ABC, abstractmethod
from models.call import CallStateModel
from models.readiness import ReadinessEnum
from persistence.icache import ICache
from typing import Optional
from uuid import UUID


class IStore(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    async def call_aget(self, call_id: UUID) -> Optional[CallStateModel]:
        pass

    @abstractmethod
    async def call_aset(self, call: CallStateModel) -> bool:
        pass

    @abstractmethod
    async def call_asearch_one(self, phone_number: str) -> Optional[CallStateModel]:
        pass

    @abstractmethod
    async def call_asearch_all(
        self,
        count: int,
        phone_number: Optional[str] = None,
    ) -> tuple[Optional[list[CallStateModel]], int]:
        pass

    def _cache_key_call_id(self, call_id: UUID) -> str:
        return f"{self.__class__.__name__}-call_id-{call_id}"

    def _cache_key_phone_number(self, phone_number: str) -> str:
        return f"{self.__class__.__name__}-phone_number-{phone_number}"
