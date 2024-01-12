from pydantic import BaseModel


class EventgridModel(BaseModel):
    resource_group: str
    subscription_id: str
    system_topic: str
