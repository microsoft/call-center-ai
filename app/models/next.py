from enum import Enum

from pydantic import BaseModel, Field


class ActionEnum(str, Enum):
    CASE_CLOSED = "case_closed"
    COMMERCIAL_OFFER = "commercial_offer"
    CUSTOMER_WILL_SEND_INFO = "customer_will_send_info"
    HIGH_PRIORITY = "high_priority"
    PROPOSE_NEW_CONTRACT = "propose_new_contract"
    REQUIRES_EXPERTISE = "requires_expertise"


class NextModel(BaseModel):
    action: ActionEnum = Field(
        description="Action to take after the call, based on the conversation, for the company."
    )
    justification: str = Field(
        description="""
        Justification for the selected action.

        # Rules
        - No more than a few sentences

        # Response examples
        - "Customer is satisfied with the service and confirmed the repair of the car is done. The case can be closed."
        - "Described damages on the roof are more important than expected. Plus, customer is not sure if the insurance policy covers this kind of damage. The company needs to send an expert to evaluate the situation."
        - "Document related to the damaged bike are missing. Documents are bike invoice, and the bike repair quote. The customer confirmed they will send them tomorrow by email."
        - "The company planned the customer taxi ride from the wrong address. The customer is not happy about this situation."
        - "The customer has many questions about the insurance policy. They are not sure if they are covered for the incident. The contract seems not to be clear about this situation."
        """
    )
