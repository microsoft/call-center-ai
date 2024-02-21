from pydantic import BaseModel


class ResourcesModel(BaseModel):
    public_url: str
