from pydantic import BaseModel, SecretStr
from pydantic_extra_types.phone_numbers import PhoneNumber


# E164 is standard accross all Microsoft services
PhoneNumber.phone_format = "E164"


class CommunicationServiceModel(BaseModel):
    access_key: SecretStr
    endpoint: str
    phone_number: PhoneNumber
    voice_name: str
