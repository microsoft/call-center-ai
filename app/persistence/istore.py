from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from aiojobs import Scheduler

from app.helpers.monitoring import start_as_current_span
from app.models.call import CallStateModel
from app.models.readiness import ReadinessEnum
from app.persistence.icache import ICache


class IStore(ABC):
    _cache: ICache

    def __init__(self, cache: ICache):
        self._cache = cache

    @abstractmethod
    @start_as_current_span("store_readiness")
    async def readiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @start_as_current_span("store_call_get")
    async def call_get(
        self,
        call_id: UUID,
    ) -> CallStateModel | None:
        pass

    @abstractmethod
    @start_as_current_span("store_call_transac")
    def call_transac(
        self,
        call: CallStateModel,
        scheduler: Scheduler,
    ) -> AbstractAsyncContextManager[None]:
        pass

    @abstractmethod
    @start_as_current_span("store_call_create")
    async def call_create(
        self,
        call: CallStateModel,
    ) -> CallStateModel:
        pass

    @abstractmethod
    @start_as_current_span("store_call_search_one")
    async def call_search_one(
        self,
        phone_number: str,
        callback_timeout: bool = True,
    ) -> CallStateModel | None:
        pass

    @abstractmethod
    @start_as_current_span("store_call_search_all")
    async def call_search_all(
        self,
        count: int,
        phone_number: str | None = None,
    ) -> tuple[list[CallStateModel] | None, int]:
        pass

    def _cache_key_call_id(self, call_id: UUID) -> str:
        return f"{self.__class__.__name__}-call_id-{call_id}"

    def _cache_key_phone_number(self, phone_number: str) -> str:
        return f"{self.__class__.__name__}-phone_number-{phone_number}"
