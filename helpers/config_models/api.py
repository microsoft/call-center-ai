from pydantic import BaseModel


class ApiModel(BaseModel):
    events_domain: str
    root_path: str = ""
