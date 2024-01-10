from pydantic import BaseModel, SecretStr


class CommunicationServiceModel(BaseModel):
    access_key: SecretStr
    endpoint: str
    phone_number: str
    voice_name: str
