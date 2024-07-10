from enum import Enum
from typing import List

from pydantic import BaseModel


class ReadinessEnum(str, Enum):
    FAIL = "fail"
    OK = "ok"


class ReadinessCheckModel(BaseModel):
    id: str
    status: ReadinessEnum


class ReadinessModel(BaseModel):
    checks: List[ReadinessCheckModel]
    status: str
