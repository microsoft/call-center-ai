from pydantic import BaseModel, SecretStr


class TwilioModel(BaseModel):
    account_sid: str
    auth_token: SecretStr
    phone_number: str
