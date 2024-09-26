from abc import ABC, abstractmethod

from helpers.monitoring import tracer
from models.readiness import ReadinessEnum


class ICache(ABC):
    @abstractmethod
    @tracer.start_as_current_span("cache_areadiness")
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @tracer.start_as_current_span("cache_aget")
    async def aget(self, key: str) -> bytes | None:
        pass

    @abstractmethod
    @tracer.start_as_current_span("cache_aset")
    async def aset(self, key: str, value: str | bytes | None, ttl_sec: int) -> bool:
        pass

    @abstractmethod
    @tracer.start_as_current_span("cache_adel")
    async def adel(self, key: str) -> bool:
        pass
