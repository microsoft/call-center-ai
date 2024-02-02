from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ReminderModel(BaseModel):
    # Immutable fields
    created_at: datetime = Field(default_factory=datetime.utcnow, frozen=True)
    # Editable fields
    description: str
    due_date_time: datetime
    owner: Optional[str] = None  # Optional for backwards compatibility
    title: str
