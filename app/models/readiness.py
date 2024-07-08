from enum import Enum
from pydantic import BaseModel
from typing import List


class ReadinessEnum(str, Enum):
    FAIL = "fail"
    OK = "ok"


class ReadinessCheckModel(BaseModel):
    id: str
    status: ReadinessEnum


class ReadinessModel(BaseModel):
    checks: List[ReadinessCheckModel]
    status: str
