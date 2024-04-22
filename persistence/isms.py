from abc import ABC, abstractmethod
from helpers.pydantic_types.phone_numbers import PhoneNumber


class ISms(ABC):
    @abstractmethod
    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        pass
