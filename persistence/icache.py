from abc import ABC, abstractmethod
from typing import Optional, Union

from models.readiness import ReadinessEnum


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
