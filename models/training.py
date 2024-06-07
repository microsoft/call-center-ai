from datetime import datetime
from functools import total_ordering
from uuid import UUID
from pydantic import BaseModel


@total_ordering
class TrainingModel(BaseModel, frozen=True):
    """
    Represents a training document from AI Search.
    """

    answer: str
    context: str
    created_at: datetime
    document_synthesis: str
    file_path: str
    id: UUID
    question: str
    score: float

    def __hash__(self) -> int:
        return self.id.__hash__()

    def __eq__(self, other):
        if not isinstance(other, TrainingModel):
            return NotImplemented
        return self.id == other.id

    def __lt__(self, other):
        if not isinstance(other, TrainingModel):
            return NotImplemented
        return self.score < other.score

    @staticmethod
    def excluded_fields_for_llm() -> set[str]:
        """
        Returns fields that should be excluded from sending to LLM because they are not relevant for document understanding.
        """
        return {"id", "file_path", "score", "created_at"}
