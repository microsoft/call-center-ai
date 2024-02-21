from pydantic import SecretStr, BaseModel, Field


class AiSearchModel(BaseModel):
    access_key: SecretStr
    endpoint: str
    expansion_k: int = Field(default=5, ge=1)
    index: str
    semantic_configuration: str
    top_k: int = Field(default=15, ge=1)
