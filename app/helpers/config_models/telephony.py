from enum import Enum
from functools import cached_property

from pydantic import BaseModel, SecretStr, ValidationInfo, field_validator

from app.helpers.pydantic_types.phone_numbers import PhoneNumber
from app.persistence.itelephony import ITelephony


class ModeEnum(str, Enum):
    AZURE_COMMUNICATION_SERVICES = "azure_communication_services"
    """Use Azure Communication Services."""
    SIP = "sip"
    """Use SIP gateway (e.g., Miralix, FreeSWITCH, Asterisk)."""


class AzureCommunicationServicesModel(BaseModel, frozen=True):
    """
    Configuration for Azure Communication Services telephony.

    Reuses the existing CommunicationServicesModel configuration.
    """

    @cached_property
    def instance(self) -> ITelephony:
        from app.helpers.config import CONFIG
        from app.persistence.azure_communication_services import (
            AzureCommunicationServicesTelephony,
        )

        return AzureCommunicationServicesTelephony(CONFIG.communication_services)


class SipModel(BaseModel, frozen=True):
    """
    Configuration for SIP gateway telephony.

    Supports generic SIP providers like Miralix, FreeSWITCH, Asterisk, etc.
    """

    gateway_host: str
    """SIP gateway hostname or IP address."""
    gateway_port: int = 5060
    """SIP gateway port (default: 5060)."""
    username: str
    """SIP username for authentication."""
    password: SecretStr
    """SIP password for authentication."""
    phone_number: PhoneNumber
    """Phone number for this SIP account."""
    transport: str = "udp"
    """SIP transport protocol: udp, tcp, or tls (default: udp)."""
    rtp_port_range_start: int = 10000
    """Starting port for RTP media (default: 10000)."""
    rtp_port_range_end: int = 20000
    """Ending port for RTP media (default: 20000)."""
    stun_server: str | None = None
    """Optional STUN server for NAT traversal (e.g., stun.l.google.com:19302)."""

    @cached_property
    def instance(self) -> ITelephony:
        from app.persistence.sip_telephony import SipTelephony

        return SipTelephony(self)


class TelephonyModel(BaseModel):
    """
    Telephony configuration with pluggable providers.

    Similar to SmsModel, allows switching between different telephony backends.
    """

    azure_communication_services: AzureCommunicationServicesModel | None = (
        AzureCommunicationServicesModel()
    )
    mode: ModeEnum = ModeEnum.AZURE_COMMUNICATION_SERVICES
    sip: SipModel | None = None

    @field_validator("azure_communication_services")
    @classmethod
    def _validate_azure_communication_services(
        cls,
        azure_communication_services: AzureCommunicationServicesModel | None,
        info: ValidationInfo,
    ) -> AzureCommunicationServicesModel | None:
        if (
            not azure_communication_services
            and info.data.get("mode", None) == ModeEnum.AZURE_COMMUNICATION_SERVICES
        ):
            raise ValueError("Azure Communication Services config required")
        return azure_communication_services

    @field_validator("sip")
    @classmethod
    def _validate_sip(
        cls,
        sip: SipModel | None,
        info: ValidationInfo,
    ) -> SipModel | None:
        if not sip and info.data.get("mode", None) == ModeEnum.SIP:
            raise ValueError("SIP config required")
        return sip

    @cached_property
    def instance(self) -> ITelephony:
        if self.mode == ModeEnum.AZURE_COMMUNICATION_SERVICES:
            assert self.azure_communication_services
            return self.azure_communication_services.instance

        assert self.sip
        return self.sip.instance
