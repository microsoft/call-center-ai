from abc import ABC, abstractmethod
from typing import Optional, Union

from helpers.monitoring import tracer
from models.readiness import ReadinessEnum


class ICache(ABC):

    @abstractmethod
    @tracer.start_as_current_span("cache_areadiness")
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @tracer.start_as_current_span("cache_aconnect")
    async def aget(self, key: str) -> Optional[bytes]:
        pass

    @abstractmethod
    @tracer.start_as_current_span("cache_aset")
    async def aset(self, key: str, value: Union[str, bytes, None]) -> bool:
        pass

    @abstractmethod
    @tracer.start_as_current_span("cache_adel")
    async def adel(self, key: str) -> bool:
        pass
