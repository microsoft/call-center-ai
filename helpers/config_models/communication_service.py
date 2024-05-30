from helpers.pydantic_types.phone_numbers import PhoneNumber
from pydantic import SecretStr, BaseModel


class CommunicationServiceModel(BaseModel):
    access_key: SecretStr
    endpoint: str
    phone_number: PhoneNumber
    queue_name: str
