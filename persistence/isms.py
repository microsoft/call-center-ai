from abc import ABC, abstractmethod
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessStatus


class ISms(ABC):

    @abstractmethod
    async def areadiness(self) -> ReadinessStatus:
        pass

    @abstractmethod
    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        pass
