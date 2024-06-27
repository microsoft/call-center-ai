from abc import ABC, abstractmethod
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessEnum


class ISms(ABC):

    @abstractmethod
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        pass
