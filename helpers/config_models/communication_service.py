from pydantic import SecretStr, BaseModel
from helpers.pydantic_types.phone_numbers import PhoneNumber


class CommunicationServiceModel(BaseModel):
    access_key: SecretStr
    endpoint: str
    phone_number: PhoneNumber
