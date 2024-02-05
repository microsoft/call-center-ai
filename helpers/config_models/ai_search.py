from pydantic import SecretStr
from pydantic_settings import BaseSettings


class AiSearchModel(BaseSettings):
    access_key: SecretStr
    endpoint: str
    expansion_k: int = 5
    index: str
    semantic_configuration: str
    top_k: int = (
        5  # More than 5, responses latency is high (> 30 secs) with GPT-4 Turbo
    )
