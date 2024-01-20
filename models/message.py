from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from typing import List


class Action(str, Enum):
    CALL = "call"
    HANGUP = "hangup"
    SMS = "sms"
    TALK = "talk"


class Persona(str, Enum):
    ASSISTANT = "assistant"
    HUMAN = "human"
    TOOL = "tool"


class ToolModel(BaseModel):
    content: str
    function_arguments: str
    function_name: str
    tool_id: str


class MessageModel(BaseModel):
    action: Action = Action.TALK
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    persona: Persona
    tool_calls: List[ToolModel] = []
