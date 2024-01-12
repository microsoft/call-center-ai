from pydantic import BaseModel


class ApiModel(BaseModel):
    root_path: str = ""
