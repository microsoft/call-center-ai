from os import environ
from pydantic import BaseModel, Field


class ApiModel(BaseModel):
    events_domain: str = environ["EVENTS_DOMAIN"]
    root_path: str = ""
    version: str = Field(default=environ["VERSION"], frozen=True)
