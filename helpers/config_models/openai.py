from pydantic import SecretStr, BaseModel
from typing import Optional


class OpenAiModel(BaseModel):
    api_key: Optional[SecretStr] = None
    endpoint: str
    gpt_backup_context: int
    gpt_backup_deployment: str
    gpt_backup_model: str
    gpt_context: int
    gpt_deployment: str
    gpt_model: str
