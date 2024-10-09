from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReminderModel(BaseModel):
    # Immutable fields
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), frozen=True)
    # Editable fields
    description: str
    due_date_time: datetime
    owner: str | None = None  # Optional for backwards compatibility
    title: str
