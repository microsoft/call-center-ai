from pydantic import SecretStr, BaseModel
from typing import Optional


class OpenAiModel(BaseModel):
    api_key: Optional[SecretStr] = None
    endpoint: str
    gpt_deployment: str
    gpt_model: str
