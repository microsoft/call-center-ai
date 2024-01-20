from pydantic_settings import BaseSettings


class CognitiveServiceModel(BaseSettings, env_prefix="cognitive_service_"):
    endpoint: str
