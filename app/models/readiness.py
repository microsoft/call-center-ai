from enum import Enum

from pydantic import BaseModel


class ReadinessEnum(str, Enum):
    FAIL = "fail"
    """The service is not ready."""
    OK = "ok"
    """The service is ready."""


class ReadinessCheckModel(BaseModel):
    id: str
    status: ReadinessEnum


class ReadinessModel(BaseModel):
    checks: list[ReadinessCheckModel]
    status: ReadinessEnum
