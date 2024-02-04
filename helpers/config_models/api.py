from pydantic_settings import BaseSettings


class ApiModel(BaseSettings):
    events_domain: str
    root_path: str = ""
