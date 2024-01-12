from pydantic import BaseModel


class WorkflowModel(BaseModel):
    agent_phone_number: str
    bot_company: str
    bot_name: str
    conversation_lang: str
