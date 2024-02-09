from pydantic import SecretStr
from pydantic_settings import BaseSettings


class AiTranslationModel(BaseSettings):
    access_key: SecretStr
    endpoint: str
