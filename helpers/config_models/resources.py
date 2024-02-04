from pydantic_settings import BaseSettings


class ResourcesModel(BaseSettings):
    public_url: str
