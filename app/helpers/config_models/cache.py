from enum import Enum
from functools import cached_property

from pydantic import BaseModel, Field, SecretStr, ValidationInfo, field_validator

from app.persistence.icache import ICache


class ModeEnum(str, Enum):
    MEMORY = "memory"
    """Use memory cache."""
    REDIS = "redis"
    """Use Redis cache."""


class MemoryModel(BaseModel, frozen=True):
    max_size: int = Field(default=128, ge=10)

    @cached_property
    def instance(self) -> ICache:
        from app.persistence.memory import (
            MemoryCache,
        )

        return MemoryCache(self)


class RedisModel(BaseModel, frozen=True):
    database: int = Field(default=0, ge=0)
    host: str
    password: SecretStr | None = None
    port: int = 6379
    ssl: bool = True

    @cached_property
    def instance(self) -> ICache:
        from app.persistence.redis import (
            RedisCache,
        )

        return RedisCache(self)


class CacheModel(BaseModel):
    memory: MemoryModel | None = MemoryModel()  # Object is fully defined by default
    mode: ModeEnum = ModeEnum.MEMORY
    redis: RedisModel | None = None

    @field_validator("redis")
    @classmethod
    def _validate_redis(
        cls,
        redis: RedisModel | None,
        info: ValidationInfo,
    ) -> RedisModel | None:
        if not redis and info.data.get("mode", None) == ModeEnum.REDIS:
            raise ValueError("Redis config required")
        return redis

    @field_validator("memory")
    @classmethod
    def _validate_memory(
        cls,
        memory: MemoryModel | None,
        info: ValidationInfo,
    ) -> MemoryModel | None:
        if not memory and info.data.get("mode", None) == ModeEnum.MEMORY:
            raise ValueError("Memory config required")
        return memory

    @cached_property
    def instance(self) -> ICache:
        if self.mode == ModeEnum.MEMORY:
            assert self.memory
            return self.memory.instance

        assert self.redis
        return self.redis.instance
