from pydantic import BaseModel
from typing import Optional


class ApiModel(BaseModel):
    events_domain: Optional[str] = None
    root_path: str = ""
