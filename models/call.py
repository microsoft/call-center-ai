from enum import Enum
from models.claim import ClaimModel
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4


class Persona(str, Enum):
    ASSISTANT = "assistant"
    HUMAN = "human"
    TOOL = "tool"


class ToolModel(BaseModel):
    content: str
    function_arguments: str
    function_name: str
    id: str


class MessageModel(BaseModel):
    content: str
    persona: Persona
    tool_calls: List[ToolModel] = []


class CallModel(BaseModel):
    claim: ClaimModel = Field(default_factory=ClaimModel)
    id: UUID = Field(default_factory=uuid4)
    messages: List[MessageModel] = []
    phone_number: str
    recognition_retry: int = Field(default=0)
