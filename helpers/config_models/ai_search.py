from pydantic import SecretStr
from pydantic_settings import BaseSettings


class AiSearchModel(BaseSettings):
    access_key: SecretStr
    endpoint: str
    index: str
    semantic_configuration: str
    top_k: int = 5
