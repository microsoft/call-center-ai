from pydantic import BaseModel


class CognitiveServiceModel(BaseModel):
    endpoint: str
    region: str
    resource_id: str
