from typing import List
from pydantic import SecretStr
from pydantic_settings import BaseSettings


class ContentSafetyModel(BaseSettings, env_prefix="content_safety_"):
    access_key: SecretStr
    blocklists: List[str]
    endpoint: str
