from abc import ABC, abstractmethod
from uuid import UUID

from app.helpers.monitoring import tracer
from app.models.call import CallStateModel
from app.models.readiness import ReadinessEnum
from app.persistence.icache import ICache


class IStore(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    @tracer.start_as_current_span("store_areadiness")
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @tracer.start_as_current_span("store_call_aget")
    async def call_aget(self, call_id: UUID) -> CallStateModel | None:
        pass

    @abstractmethod
    @tracer.start_as_current_span("store_call_aset")
    async def call_aset(self, call: CallStateModel) -> bool:
        pass

    @abstractmethod
    @tracer.start_as_current_span("store_call_asearch_one")
    async def call_asearch_one(self, phone_number: str) -> CallStateModel | None:
        pass

    @abstractmethod
    @tracer.start_as_current_span("store_call_asearch_all")
    async def call_asearch_all(
        self,
        count: int,
        phone_number: str | None = None,
    ) -> tuple[list[CallStateModel] | None, int]:
        pass

    def _cache_key_call_id(self, call_id: UUID) -> str:
        return f"{self.__class__.__name__}-call_id-{call_id}"

    def _cache_key_phone_number(self, phone_number: str) -> str:
        return f"{self.__class__.__name__}-phone_number-{phone_number}"
