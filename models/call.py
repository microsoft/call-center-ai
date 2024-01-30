from datetime import datetime
from models.claim import ClaimModel
from models.message import MessageModel
from models.next import NextModel
from models.reminder import ReminderModel
from models.synthesis import SynthesisModel
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4


class CallModel(BaseModel):
    # Immutable fields
    call_id: UUID = Field(default_factory=uuid4, frozen=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    # Editable fields
    claim: ClaimModel = Field(default_factory=ClaimModel)
    messages: List[MessageModel] = []
    next: Optional[NextModel] = None
    phone_number: str
    recognition_retry: int = Field(default=0)
    reminders: List[ReminderModel] = []
    synthesis: Optional[SynthesisModel] = None
