from enum import Enum
from functools import cached_property

from pydantic import BaseModel, SecretStr, ValidationInfo, field_validator

from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.persistence.isms import ISms


class ModeEnum(str, Enum):
    COMMUNICATION_SERVICES = "communication_services"
    """Use Communication Services."""
    TWILIO = "twilio"
    """Use Twilio."""


class CommunicationServiceModel(BaseModel, frozen=True):
    """
    Represents the configuration for the Communication Services API.

    Model is purely empty to fit to the `ISms` interface and the "mode" enum code organization. As the Communication Services is also used as the only call interface, it is not necessary to duplicate the models.
    """

    @cached_property
    def instance(self) -> ISms:
        from app.helpers.config import CONFIG
        from app.persistence.communication_services import (
            CommunicationServicesSms,
        )

        return CommunicationServicesSms(CONFIG.communication_services)


class TwilioModel(BaseModel, frozen=True):
    account_sid: str
    auth_token: SecretStr
    phone_number: PhoneNumber

    @cached_property
    def instance(self) -> ISms:
        from app.persistence.twilio import (
            TwilioSms,
        )

        return TwilioSms(self)


class SmsModel(BaseModel):
    communication_services: CommunicationServiceModel | None = (
        CommunicationServiceModel()
    )  # Object is fully defined by default
    mode: ModeEnum = ModeEnum.COMMUNICATION_SERVICES
    twilio: TwilioModel | None = None

    @field_validator("communication_services")
    @classmethod
    def _validate_communication_services(
        cls,
        communication_services: CommunicationServiceModel | None,
        info: ValidationInfo,
    ) -> CommunicationServiceModel | None:
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
        twilio: TwilioModel | None,
        info: ValidationInfo,
    ) -> TwilioModel | None:
        if not twilio and info.data.get("mode", None) == ModeEnum.TWILIO:
            raise ValueError("Twilio config required")
        return twilio

    @cached_property
    def instance(self) -> ISms:
        if self.mode == ModeEnum.COMMUNICATION_SERVICES:
            assert self.communication_services
            return self.communication_services.instance

        assert self.twilio
        return self.twilio.instance
