from datetime import datetime
from pydantic import BaseModel, Field


class ReminderModel(BaseModel):
    created_at: datetime = Field(default_factory=datetime.utcnow)
    description: str
    due_date_time: str
    title: str
