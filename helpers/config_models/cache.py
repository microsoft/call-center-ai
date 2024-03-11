from enum import Enum
from functools import cache
from persistence.icache import ICache
from pydantic import field_validator, SecretStr, BaseModel, Field, ValidationInfo
from typing import Optional


class ModeEnum(str, Enum):
    MEMORY = "memory"
    REDIS = "redis"


class MemoryModel(BaseModel, frozen=True):
    max_size: int = Field(default=100, ge=10)


class RedisModel(BaseModel, frozen=True):
    database: int = Field(default=0, ge=0)
    host: str
    password: SecretStr
    port: int = 6379
    ssl: bool = True


class CacheModel(BaseModel, frozen=True):
    memory: Optional[MemoryModel] = None
    mode: ModeEnum = ModeEnum.MEMORY
    redis: Optional[RedisModel] = None

    @field_validator("redis")
    def validate_sqlite(
        cls,
        redis: Optional[RedisModel],
        info: ValidationInfo,
    ) -> Optional[RedisModel]:
        if not redis and info.data.get("mode", None) == ModeEnum.REDIS:
            raise ValueError("Redis config required")
        return redis

    @field_validator("memory")
    def validate_memory(
        cls,
        memory: Optional[MemoryModel],
        info: ValidationInfo,
    ) -> Optional[MemoryModel]:
        if not memory and info.data.get("mode", None) == ModeEnum.MEMORY:
            raise ValueError("Memory config required")
        return memory

    @cache
    def instance(self) -> ICache:
        if self.mode == ModeEnum.MEMORY:
            from persistence.memory import MemoryCache

            assert self.memory
            return MemoryCache(self.memory)

        from persistence.redis import RedisCache

        assert self.redis
        return RedisCache(self.redis)
