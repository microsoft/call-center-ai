from abc import ABC, abstractmethod

from app.helpers.monitoring import tracer
from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.models.readiness import ReadinessEnum


class ISms(ABC):
    @abstractmethod
    @tracer.start_as_current_span("sms_readiness")
    async def readiness(self) -> ReadinessEnum:
        pass

    @abstractmethod
    @tracer.start_as_current_span("sms_send")
    async def send(self, content: str, phone_number: PhoneNumber) -> bool:
        pass
