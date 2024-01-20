from pydantic import BaseModel


class SynthesisModel(BaseModel):
    long: str
    short: str
