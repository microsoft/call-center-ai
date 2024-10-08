from pydantic import BaseModel


class ErrorInnerModel(BaseModel):
    message: str
    details: list[str]


class ErrorModel(BaseModel):
    error: ErrorInnerModel
