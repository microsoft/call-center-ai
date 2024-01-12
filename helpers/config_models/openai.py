from pydantic import BaseModel


class OpenAiModel(BaseModel):
    endpoint: str
    gpt_deployment: str
    gpt_model: str
