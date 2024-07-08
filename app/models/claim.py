from enum import Enum
from pydantic import BaseModel
from typing import Optional


class ClaimTypeEnum(str, Enum):
    DATETIME = "datetime"
    EMAIL = "email"
    PHONE_NUMBER = "phone_number"
    TEXT = "text"


class ClaimFieldModel(BaseModel):
    description: Optional[str] = None
    name: str
    type: ClaimTypeEnum
