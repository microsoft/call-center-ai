from models.action import Style as StyleAction
from pydantic import BaseModel


class LlmContentModel(BaseModel):
    style: StyleAction = StyleAction.CHEERFUL
    text: str
