from enum import Enum
from pydantic import BaseModel
from typing import Optional


class Indent(str, Enum):
    CONTINUE = "continue"
    END_CALL = "end_call"
    NEW_CLAIM = "new_claim"
    NEW_OR_UPDATED_REMINDER = "new_or_updated_reminder"
    TALK_TO_HUMAN = "talk_to_human"
    UPDATED_CLAIM = "updated_claim"


class ActionModel(BaseModel):
    content: str
    intent: Indent
