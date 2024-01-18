from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ReminderModel(BaseModel):
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    description: str
    due_date_time: str
    owner: Optional[str] = None  # Optional for backwards compatibility
    title: str
