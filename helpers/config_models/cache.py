from enum import Enum
from pydantic import validator, SecretStr, BaseModel, Field
from typing import Optional


class ModeEnum(str, Enum):
    MEMORY = "memory"
    REDIS = "redis"


class MemoryModel(BaseModel):
    max_size: int = Field(default=100, ge=10)


class RedisModel(BaseModel):
    database: int = Field(default=0, ge=0)
    host: str
    password: SecretStr
    port: int = 6379
    ssl: bool = True


class CacheModel(BaseModel):
    memory: Optional[MemoryModel] = None
    mode: ModeEnum = ModeEnum.MEMORY
    redis: Optional[RedisModel] = None

    @validator("redis", always=True)
    def validate_sqlite(
        cls, v: Optional[RedisModel], values, **kwargs
    ) -> Optional[RedisModel]:
        if not v and values.get("mode", None) == ModeEnum.REDIS:
            raise ValueError("Redis config required")
        return v

    @validator("memory", always=True)
    def validate_memory(
        cls, v: Optional[MemoryModel], values, **kwargs
    ) -> Optional[MemoryModel]:
        if not v and values.get("mode", None) == ModeEnum.MEMORY:
            raise ValueError("Memory config required")
        return v
