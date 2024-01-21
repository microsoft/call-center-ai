from helpers.config_models.api import ApiModel
from helpers.config_models.cognitive_service import CognitiveServiceModel
from helpers.config_models.communication_service import CommunicationServiceModel
from helpers.config_models.database import DatabaseModel
from helpers.config_models.monitoring import MonitoringModel
from helpers.config_models.openai import OpenAiModel
from helpers.config_models.prompts import PromptsModel
from helpers.config_models.resources import ResourcesModel
from helpers.config_models.workflow import WorkflowModel
from pydantic import Field
from pydantic_settings import BaseSettings


class RootModel(BaseSettings, env_prefix=""):
    # Immutable fields
    version: str = Field(frozen=True)
    # Editable fields
    api: ApiModel
    cognitive_service: CognitiveServiceModel
    communication_service: CommunicationServiceModel
    database: DatabaseModel
    monitoring: MonitoringModel = (
        MonitoringModel()
    )  # Object is fully defined by default
    openai: OpenAiModel
    prompts: PromptsModel = PromptsModel()  # Object is fully defined by default
    resources: ResourcesModel
    workflow: WorkflowModel
