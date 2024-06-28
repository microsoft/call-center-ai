from enum import Enum
from pydantic import BaseModel, Field


class SatisfactionEnum(str, Enum):
    TERRIBLE = "terrible"  # 1
    LOW = "low"  # 2
    PARTIAL = "partial"  # 3
    HIGH = "high"  # 4
    UNKNOW = "unknow"


class SynthesisModel(BaseModel):
    long: str = Field(
        description="""
        Summarize the call with the customer in a paragraph. The customer cannot reply to this message, but will read it in their web portal.

        # Rules
        - Do not include details of the call process
        - Do not include personal details (e.g., name, phone number, address)
        - Include details stored in the claim, to make the customer confident that the situation is understood
        - Prefer including details about the situation (e.g., what, when, where, how)
        - Say "you" to refer to the customer, and "I" to refer to the assistant
        - Use Markdown syntax to format the message with paragraphs, bold text, and URL
        """
    )
    satisfaction: SatisfactionEnum = Field(
        description="How satisfied is the customer with the call."
    )
    short: str = Field(
        description="""
        Summarize the call with the customer in a few words. The customer cannot reply to this message, but will read it in their web portal.

        # Rules
        - Prefix the response with a determiner (e.g., "the theft of your car", "your broken window")

        # Response examples
        - "the breakdown of your scooter"
        - "the flooding in your field"
        - "the theft of your car"
        - "the water damage in your kitchen"
        - "your broken window"
        """
    )
    improvement_suggestions: str = Field(
        description="""
        Provide suggestions to improve the customer experience during the call.

        # Rules
        - Include suggestions to improve the call process, the assistant's behavior, or the company's service
        - No more than a few sentences
        """
    )
