from pydantic_settings import BaseSettings


class CognitiveServiceModel(BaseSettings):
    endpoint: str
