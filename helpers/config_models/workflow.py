from pydantic_extra_types.phone_numbers import PhoneNumber
from pydantic_settings import BaseSettings


# E164 is standard accross all Microsoft services
PhoneNumber.phone_format = "E164"


class WorkflowModel(BaseSettings, env_prefix="workflow_"):
    agent_phone_number: PhoneNumber
    bot_company: str
    bot_name: str
    conversation_lang: str = "fr-FR"  # French
    conversation_timeout_hour: int = 72  # 3 days
    intelligence_hard_timeout_sec: int = 180  # 3 minutes
    intelligence_soft_timeout_sec: int = 30  # 30 seconds
