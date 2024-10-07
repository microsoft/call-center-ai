from pydantic import BaseModel


class AppConfigurationModel(BaseModel):
    endpoint: str
    ttl_sec: int = 60  # 1 min
