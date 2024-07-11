from pydantic import BaseModel, SecretStr


class AiTranslationModel(BaseModel):
    access_key: SecretStr
    endpoint: str
