from datetime import datetime
from enum import Enum
from models.claim import ClaimModel
from models.reminder import ReminderModel
from pydantic import BaseModel, Field
from typing import List
from uuid import UUID, uuid4


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
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    persona: Persona
    tool_calls: List[ToolModel] = []


class CallModel(BaseModel):
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    claim: ClaimModel = Field(default_factory=ClaimModel)
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    messages: List[MessageModel] = []
    phone_number: str
    recognition_retry: int = Field(default=0)
    reminders: List[ReminderModel] = []
