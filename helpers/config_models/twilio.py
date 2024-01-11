from pydantic import BaseModel, SecretStr
from pydantic_extra_types.phone_numbers import PhoneNumber


# E164 is standard accross all Microsoft services
PhoneNumber.phone_format = "E164"


class TwilioModel(BaseModel):
    account_sid: str
    auth_token: SecretStr
    phone_number: PhoneNumber
