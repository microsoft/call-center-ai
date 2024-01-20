from pydantic_settings import BaseSettings


class ResourcesModel(BaseSettings, env_prefix="resources_"):
    public_url: str
