from abc import ABC, abstractmethod

from helpers.monitoring import tracer
from helpers.pydantic_types.phone_numbers import PhoneNumber
from models.readiness import ReadinessEnum


class ISms(ABC):

    @abstractmethod
    @tracer.start_as_current_span("sms_areadiness")
    async def areadiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @tracer.start_as_current_span("sms_asend")
    async def asend(self, content: str, phone_number: PhoneNumber) -> bool:
        pass
