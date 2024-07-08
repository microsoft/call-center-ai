from helpers.config_models.ai_search import AiSearchModel
from helpers.config_models.ai_translation import AiTranslationModel
from helpers.config_models.cache import CacheModel
from helpers.config_models.cognitive_service import CognitiveServiceModel
from helpers.config_models.communication_services import CommunicationServicesModel
from helpers.config_models.database import DatabaseModel
from helpers.config_models.llm import LlmModel
from helpers.config_models.monitoring import MonitoringModel
from helpers.config_models.prompts import PromptsModel
from helpers.config_models.resources import ResourcesModel
from helpers.config_models.sms import SmsModel
from helpers.config_models.conversation import ConversationModel
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RootModel(BaseSettings):
    # Pydantic settings
    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        env_nested_delimiter="__",
        env_prefix="",
    )

    # Immutable fields
    public_domain: str = Field(frozen=True)
    version: str = Field(default="0.0.0-unknown", frozen=True)
    # Editable fields
    ai_search: AiSearchModel
    ai_translation: AiTranslationModel
    cache: CacheModel = CacheModel()  # Object is fully defined by default
    cognitive_service: CognitiveServiceModel
    communication_services: CommunicationServicesModel = Field(
        serialization_alias="communication_service",  # Compatibility with v5
    )
    database: DatabaseModel = DatabaseModel()  # Object is fully defined by default
    llm: LlmModel
    monitoring: MonitoringModel = (
        MonitoringModel()
    )  # Object is fully defined by default
    prompts: PromptsModel = PromptsModel()  # Object is fully defined by default
    resources: ResourcesModel
    sms: SmsModel = SmsModel()  # Object is fully defined by default
    conversation: ConversationModel = Field(
        serialization_alias="workflow"
    )  # Compatibility with v7
