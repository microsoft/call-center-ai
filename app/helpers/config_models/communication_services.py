from pydantic import BaseModel, SecretStr

from app.helpers.pydantic_types.phone_numbers import PhoneNumber


class CommunicationServicesModel(BaseModel):
    access_key: SecretStr
    endpoint: str
    phone_number: PhoneNumber
    recording_container_url: str
    resource_id: str
