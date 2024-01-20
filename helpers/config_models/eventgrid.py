from pydantic_settings import BaseSettings


class EventgridModel(BaseSettings, env_prefix="eventgrid_"):
    resource_group: str
    subscription_id: str
    system_topic: str
