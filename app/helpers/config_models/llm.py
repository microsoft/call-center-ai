from azure.ai.inference.aio import ChatCompletionsClient
from pydantic import BaseModel

from app.helpers.cache import lru_acache
from app.helpers.http import azure_transport
from app.helpers.identity import credential


class DeploymentModel(BaseModel, frozen=True):
    api_version: str = "2024-10-21"  # See: https://learn.microsoft.com/en-us/azure/ai-services/openai/reference#api-specs
    context: int
    endpoint: str
    model: str
    seed: int = 42  # Reproducible results
    temperature: float = 0.0  # Most focused and deterministic

    @lru_acache()
    async def client(self) -> tuple[ChatCompletionsClient, "DeploymentModel"]:
        return ChatCompletionsClient(
            # Reliability
            seed=self.seed,
            temperature=self.temperature,
            # Deployment
            api_version=self.api_version,
            endpoint=self.endpoint,
            model=self.model,
            # Performance
            transport=await azure_transport(),
            # Authentication
            credential_scopes=["https://cognitiveservices.azure.com/.default"],
            credential=await credential(),
        ), self


class LlmModel(BaseModel):
    fast: DeploymentModel
    slow: DeploymentModel

    def selected(self, is_fast: bool) -> DeploymentModel:
        return self.fast if is_fast else self.slow
