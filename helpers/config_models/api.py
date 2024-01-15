from os import environ
from pydantic import BaseModel, Field


class ApiModel(BaseModel):
    root_path: str = ""
    version: str = Field(default=environ["VERSION"], frozen=True)
