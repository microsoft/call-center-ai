from abc import ABC, abstractmethod
from typing import Optional, Union


class ICache(ABC):
    @abstractmethod
    async def aget(self, key: Union[str, bytes]) -> Optional[bytes]:
        pass

    @abstractmethod
    async def aset(
        self, key: Union[str, bytes], value: Union[str, bytes, None]
    ) -> bool:
        pass
