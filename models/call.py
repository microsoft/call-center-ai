from datetime import datetime
from models.claim import ClaimModel
from models.message import MessageModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4


class CallModel(BaseModel):
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    claim: ClaimModel = Field(default_factory=ClaimModel)
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    messages: List[MessageModel] = []
    phone_number: str
    recognition_retry: int = Field(default=0)
    reminders: List[ReminderModel] = []
    synthesis: Optional[SynthesisModel] = None
