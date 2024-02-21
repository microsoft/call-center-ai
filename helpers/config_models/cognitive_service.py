from pydantic import BaseModel


class CognitiveServiceModel(BaseModel):
    endpoint: str
