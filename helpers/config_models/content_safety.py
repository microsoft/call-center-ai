from typing import List
from pydantic import SecretStr
from pydantic_settings import BaseSettings


class ContentSafetyModel(BaseSettings):
    access_key: SecretStr
    blocklists: List[str]
    endpoint: str
