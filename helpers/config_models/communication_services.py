from pydantic import BaseModel, SecretStr, ValidationInfo, field_validator

from helpers.pydantic_types.phone_numbers import PhoneNumber


class CommunicationServicesModel(BaseModel):
    access_key: SecretStr
    call_queue_name: str
    endpoint: str
    phone_number: PhoneNumber
    post_queue_name: str
    recording_container_url: str | None = None
    recording_enabled: bool
    resource_id: str
    sms_queue_name: str
    trainings_queue_name: str

    @field_validator("recording_container_url")
    @classmethod
    def _validate_recording_container_url(
        cls,
        recording_container_url: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if not recording_container_url and info.data.get("recording_enabled", False):
            raise ValueError("Recording container URL required")
        return recording_container_url
