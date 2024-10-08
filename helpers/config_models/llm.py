from abc import abstractmethod
from enum import Enum
from typing import Any

from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import BaseModel, Field, SecretStr, ValidationInfo, field_validator

from helpers.identity import token


class ModeEnum(str, Enum):
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"


class AbstractPlatformModel(BaseModel):
    _client_kwargs: dict[str, Any] = {
        # Reliability
        "max_retries": 0,  # Retries are managed manually
        "timeout": 60,
    }
    context: int
    model: str
    seed: int = 42  # Reproducible results
    streaming: bool
    temperature: float = 0.0  # Most focused and deterministic

    @abstractmethod
    async def instance(
        self,
    ) -> tuple[AsyncAzureOpenAI | AsyncOpenAI, "AbstractPlatformModel"]:
        pass


class AzureOpenaiPlatformModel(AbstractPlatformModel):
    _client: AsyncAzureOpenAI | None = None
    api_version: str = "2024-06-01"
    deployment: str
    endpoint: str

    async def instance(self) -> tuple[AsyncAzureOpenAI, AbstractPlatformModel]:
        if not self._client:
            self._client = AsyncAzureOpenAI(
                **self._client_kwargs,
                # Deployment
                api_version=self.api_version,
                azure_deployment=self.deployment,
                azure_endpoint=self.endpoint,
                # Authentication
                azure_ad_token_provider=await token(
                    "https://cognitiveservices.azure.com/.default"
                ),
            )
        return self._client, self


class OpenaiPlatformModel(AbstractPlatformModel):
    _client: AsyncOpenAI | None = None
    api_key: SecretStr
    endpoint: str

    async def instance(self) -> tuple[AsyncOpenAI, AbstractPlatformModel]:
        if not self._client:
            self._client = AsyncOpenAI(
                **self._client_kwargs,
                # API root URL
                base_url=self.endpoint,
                # Authentication
                api_key=self.api_key.get_secret_value(),
            )
        return self._client, self


class SelectedPlatformModel(BaseModel):
    azure_openai: AzureOpenaiPlatformModel | None = None
    mode: ModeEnum
    openai: OpenaiPlatformModel | None = None

    @field_validator("azure_openai")
    @classmethod
    def _validate_azure_openai(
        cls,
        azure_openai: AzureOpenaiPlatformModel | None,
        info: ValidationInfo,
    ) -> AzureOpenaiPlatformModel | None:
        if not azure_openai and info.data.get("mode", None) == ModeEnum.AZURE_OPENAI:
            raise ValueError("Azure OpenAI config required")
        return azure_openai

    @field_validator("openai")
    @classmethod
    def _validate_openai(
        cls,
        openai: OpenaiPlatformModel | None,
        info: ValidationInfo,
    ) -> OpenaiPlatformModel | None:
        if not openai and info.data.get("mode", None) == ModeEnum.OPENAI:
            raise ValueError("OpenAI config required")
        return openai

    def selected(self) -> AzureOpenaiPlatformModel | OpenaiPlatformModel:
        platform = (
            self.azure_openai if self.mode == ModeEnum.AZURE_OPENAI else self.openai
        )
        assert platform
        return platform


class LlmModel(BaseModel):
    fast: SelectedPlatformModel = Field(
        serialization_alias="backup",  # Backwards compatibility with v6
    )
    slow: SelectedPlatformModel = Field(
        serialization_alias="primary",  # Backwards compatibility with v6
    )

    def selected(self, is_fast: bool) -> AzureOpenaiPlatformModel | OpenaiPlatformModel:
        platform = self.fast if is_fast else self.slow
        return platform.selected()
