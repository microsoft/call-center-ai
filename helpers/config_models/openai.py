from pydantic import BaseModel, HttpUrl


class OpenAiModel(BaseModel):
    endpoint: HttpUrl
    gpt_deployment: str
    gpt_model: str
