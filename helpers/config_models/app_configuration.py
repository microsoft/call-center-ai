from pydantic import BaseModel, SecretStr


class AppConfigurationModel(BaseModel):
    connection_string: SecretStr
    ttl_sec: int = 60  # 1 min
