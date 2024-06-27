from pydantic import BaseModel, Field


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
    short: str = Field(
        description="""
        Summarize the call with the customer in a few words. The customer cannot reply to this message, but will read it in their web portal.

        # Rules
        - Prefix the answer with a determiner (e.g., "the theft of your car", "your broken window")

        # Response examples
        - "the breakdown of your scooter"
        - "the flooding in your field"
        - "the theft of your car"
        - "the water damage in your kitchen"
        - "your broken window"
        """
    )
