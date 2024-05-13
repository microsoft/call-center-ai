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

    @cache
    def instance(self) -> ICache:
        from persistence.memory import MemoryCache

        return MemoryCache(self)


class RedisModel(BaseModel, frozen=True):
    database: int = Field(default=0, ge=0)
    host: str
    password: SecretStr
    port: int = 6379
    ssl: bool = True

    @cache
    def instance(self) -> ICache:
        from persistence.redis import RedisCache

        return RedisCache(self)


class CacheModel(BaseModel):
    memory: Optional[MemoryModel] = MemoryModel()  # Object is fully defined by default
    mode: ModeEnum = ModeEnum.MEMORY
    redis: Optional[RedisModel] = None

    @field_validator("redis")
    def _validate_sqlite(
        cls,
        redis: Optional[RedisModel],
        info: ValidationInfo,
    ) -> Optional[RedisModel]:
        if not redis and info.data.get("mode", None) == ModeEnum.REDIS:
            raise ValueError("Redis config required")
        return redis

    @field_validator("memory")
    def _validate_memory(
        cls,
        memory: Optional[MemoryModel],
        info: ValidationInfo,
    ) -> Optional[MemoryModel]:
        if not memory and info.data.get("mode", None) == ModeEnum.MEMORY:
            raise ValueError("Memory config required")
        return memory

    def instance(self) -> ICache:
        if self.mode == ModeEnum.MEMORY:
            assert self.memory
            return self.memory.instance()

        assert self.redis
        return self.redis.instance()
