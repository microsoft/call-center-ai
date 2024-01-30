from enum import Enum
from pydantic import BaseModel


class Action(str, Enum):
    CASE_CLOSED = "case_closed"
    COMMERCIAL_OFFER = "commercial_offer"
    CUSTOMER_WILL_SEND_INFO = "customer_will_send_info"
    HIGH_PRIORITY = "high_priority"
    PROPOSE_NEW_CONTRACT = "propose_new_contract"
    REQUIRES_EXPERTISE = "requires_expertise"


class NextModel(BaseModel):
    action: Action
    justification: str
