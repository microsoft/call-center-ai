from pydantic import SecretStr, BaseModel


class AiTranslationModel(BaseModel):
    access_key: SecretStr
    endpoint: str
