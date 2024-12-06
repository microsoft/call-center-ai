from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from app.helpers.config_models.ai_search import AiSearchModel
from app.helpers.config_models.ai_translation import AiTranslationModel
from app.helpers.config_models.app_configuration import AppConfigurationModel
from app.helpers.config_models.cache import CacheModel
from app.helpers.config_models.cognitive_service import CognitiveServiceModel
from app.helpers.config_models.communication_services import CommunicationServicesModel
from app.helpers.config_models.conversation import ConversationModel
from app.helpers.config_models.database import DatabaseModel
from app.helpers.config_models.llm import LlmModel
from app.helpers.config_models.monitoring import MonitoringModel
from app.helpers.config_models.prompts import PromptsModel
from app.helpers.config_models.queue import QueueModel
from app.helpers.config_models.resources import ResourcesModel
from app.helpers.config_models.sms import SmsModel


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
    database: DatabaseModel
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
    app_configuration: AppConfigurationModel
    queue: QueueModel

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Customise the order of the settings sources.

        Order is now:
        1. Environment variables
        2. .env file
        3. Docker secrets
        4. Initial settings

        See: https://docs.pydantic.dev/latest/concepts/pydantic_settings/#changing-priority
        """
        return env_settings, dotenv_settings, file_secret_settings, init_settings
