from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import BaseModel, SecretStr, ValidationInfo, field_validator

from helpers.pydantic_types.phone_numbers import PhoneNumber
from persistence.isms import ISms


class ModeEnum(str, Enum):
    COMMUNICATION_SERVICES = "communication_services"
    TWILIO = "twilio"


class CommunicationServiceModel(BaseModel, frozen=True):
    """
    Represents the configuration for the Communication Services API.

    Model is purely empty to fit to the `ISms` interface and the "mode" enum code organization. As the Communication Services is also used as the only call interface, it is not necessary to duplicate the models.
    """

    @lru_cache(maxsize=None)  # pylint: disable=method-cache-max-size-none
    def instance(self) -> ISms:
        from helpers.config import CONFIG  # pylint: disable=import-outside-toplevel
        from persistence.communication_services import (  # pylint: disable=import-outside-toplevel
            CommunicationServicesSms,
        )

        return CommunicationServicesSms(CONFIG.communication_services)


class TwilioModel(BaseModel, frozen=True):
    account_sid: str
    auth_token: SecretStr
    phone_number: PhoneNumber

    @lru_cache(maxsize=None)  # pylint: disable=method-cache-max-size-none
    def instance(self) -> ISms:
        from persistence.twilio import (  # pylint: disable=import-outside-toplevel
            TwilioSms,
        )

        return TwilioSms(self)


class SmsModel(BaseModel):
    communication_services: Optional[CommunicationServiceModel] = (
        CommunicationServiceModel()
    )  # Object is fully defined by default
    mode: ModeEnum = ModeEnum.COMMUNICATION_SERVICES
    twilio: Optional[TwilioModel] = None

    @field_validator("communication_services")
    @classmethod
    def _validate_communication_services(
        cls,
        communication_services: Optional[CommunicationServiceModel],
        info: ValidationInfo,
    ) -> Optional[CommunicationServiceModel]:
        if (
            not communication_services
            and info.data.get("mode", None) == ModeEnum.COMMUNICATION_SERVICES
        ):
            raise ValueError("Communication Services config required")
        return communication_services

    @field_validator("twilio")
    @classmethod
    def _validate_twilio(
        cls,
        twilio: Optional[TwilioModel],
        info: ValidationInfo,
    ) -> Optional[TwilioModel]:
        if not twilio and info.data.get("mode", None) == ModeEnum.TWILIO:
            raise ValueError("Twilio config required")
        return twilio

    def instance(self) -> ISms:
        if self.mode == ModeEnum.COMMUNICATION_SERVICES:
            assert self.communication_services
            return self.communication_services.instance()

        assert self.twilio
        return self.twilio.instance()
