from abc import ABC, abstractmethod
from models.readiness import ReadinessEnum
from typing import Optional, Union


class ICache(ABC):

    @abstractmethod
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    async def aget(self, key: str) -> Optional[bytes]:
        pass

    @abstractmethod
    async def aset(self, key: str, value: Union[str, bytes, None]) -> bool:
        pass

    @abstractmethod
    async def adel(self, key: str) -> bool:
        pass
