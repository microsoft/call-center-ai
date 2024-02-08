from pydantic_settings import BaseSettings


class ApiModel(BaseSettings, env_prefix="api_"):
    events_domain: str
