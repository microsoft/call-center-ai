from enum import Enum
from pydantic import BaseModel
from typing import Optional


class Indent(str, Enum):
    CONTINUE = "continue"
    END_CALL = "end_call"
    TALK_TO_HUMAN = "talk_to_human"
    UPDATE_CLAIM = "update_claim"


class ActionModel(BaseModel):
    content: str
    intent: Indent
