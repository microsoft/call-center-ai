from functools import total_ordering
from pydantic import BaseModel, Field
from typing import Optional


@total_ordering
class TrainingModel(BaseModel):
    """
    Represents a training document from Azure Search.

    Must include a field "vectors", type "Collection(Edm.Single)" with 1536 dimensions, searchable only.
    """

    # Immutable fields
    id: str = Field(frozen=True)  # Type: Edm.String, attributes: retrievable
    # Editable fields
    content: str  # Type: Edm.String, attributes: retrievable, searchable
    source_uri: Optional[str] = None  # Type: Edm.String, attributes: retrievable
    title: str  # Type: Edm.String, attributes: retrievable, searchable
    # On search
    score: float = Field(frozen=True)

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
