from pydantic import SecretStr, Field, BaseModel


class ContentSafetyModel(BaseModel):
    access_key: SecretStr
    blocklists: list[str] = []
    category_hate_score: int = Field(default=0, ge=0, le=7)
    category_self_harm_score: int = Field(default=0, ge=0, le=7)
    category_sexual_score: int = Field(default=2, ge=0, le=7)
    category_violence_score: int = Field(default=0, ge=0, le=7)
    endpoint: str
