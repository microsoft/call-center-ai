from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ClaimTypeEnum(str, Enum):
    DATETIME = "datetime"
    EMAIL = "email"
    PHONE_NUMBER = "phone_number"
    TEXT = "text"


class ClaimFieldModel(BaseModel):
    description: Optional[str] = None
    name: str
    type: ClaimTypeEnum
