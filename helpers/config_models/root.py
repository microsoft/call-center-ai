from helpers.config_models.api import ApiModel
from helpers.config_models.cognitive_service import CognitiveServiceModel
from helpers.config_models.communication_service import CommunicationServiceModel
from helpers.config_models.eventgrid import EventgridModel
from helpers.config_models.monitoring import MonitoringModel
from helpers.config_models.openai import OpenAiModel
from helpers.config_models.resources import ResourcesModel
from helpers.config_models.twilio import TwilioModel
from helpers.config_models.workflow import WorkflowModel
from pydantic import BaseModel


class RootModel(BaseModel):
    api: ApiModel
    cognitive_service: CognitiveServiceModel
    communication_service: CommunicationServiceModel
    eventgrid: EventgridModel
    monitoring: MonitoringModel
    openai: OpenAiModel
    resources: ResourcesModel
    twilio: TwilioModel
    workflow: WorkflowModel
