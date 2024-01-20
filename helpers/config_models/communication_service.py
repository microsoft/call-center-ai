from pydantic import SecretStr
from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic_settings import BaseSettings


# E164 is standard accross all Microsoft services
PhoneNumber.phone_format = "E164"


class CommunicationServiceModel(BaseSettings, env_prefix="communication_service_"):
    access_key: SecretStr
    endpoint: str
    phone_number: PhoneNumber
    voice_name: str
