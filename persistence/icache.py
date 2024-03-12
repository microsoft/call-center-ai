from abc import ABC, abstractmethod
from typing import Optional, Union


class ICache(ABC):
    @abstractmethod
    async def aget(self, key: str) -> Optional[bytes]:
        pass

    @abstractmethod
    async def aset(self, key: str, value: Union[str, bytes, None]) -> bool:
        pass
