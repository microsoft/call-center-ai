from enum import Enum
from pydantic import BaseModel


class Action(str, Enum):
    CASE_CLOSED = "case_closed"
    COMMERCIAL_OFFER = "commercial_offer"
    IN_DEPTH_STUDY = "in_depth_study"
    PROPOSE_NEW_CONTRACT = "propose_new_contract"


class NextModel(BaseModel):
    action: Action
    justification: str
