from abc import abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from enum import Enum

from azure.core.credentials import AzureKeyCredential
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import BaseModel, SecretStr, ValidationInfo, field_validator
from rtclient import (
    RTClient,
)

from app.helpers.identity import credential, token


class ModeEnum(str, Enum):
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"


class AbstractPlatformModel(BaseModel):
    """
    Shared properties for all LLM platform models.
    """

    context: int
    model: str


class AbstractRealtimePlatformModel(AbstractPlatformModel):
    temperature: float = 0.6  # 0.6 is the minimum as of Nov 5 2024

    @abstractmethod
    @asynccontextmanager
    def instance(
        self,
    ) -> AsyncGenerator[tuple[RTClient, "AbstractRealtimePlatformModel"], None]:
        pass


class AzureOpenaiRealtimePlatformModel(AbstractRealtimePlatformModel):
    """
    Properties for the realtime LLM models, like `gpt-4o-realtime`, hosted on Azure.
    """

    deployment: str
    endpoint: str

    @asynccontextmanager
    async def instance(
        self,
    ) -> AsyncGenerator[tuple[RTClient, "AzureOpenaiRealtimePlatformModel"], None]:
        async with RTClient(
            # Deployment
            azure_deployment=self.deployment,
            model=self.model,
            url=self.endpoint,
            # Authentication
            token_credential=await credential(),
        ) as client:
            yield client, self


class OpenaiRealtimePlatformModel(AbstractRealtimePlatformModel):
    """
    Properties for the realtime LLM models, like `gpt-4o-realtime`, hosted on OpenAI.
    """

    api_key: SecretStr

    @asynccontextmanager
    async def instance(
        self,
    ) -> AsyncGenerator[tuple[RTClient, "OpenaiRealtimePlatformModel"], None]:
        async with RTClient(
            # Deployment
            model=self.model,
            # Authentication
            key_credential=AzureKeyCredential(self.api_key.get_secret_value()),
        ) as client:
            yield client, self


class SelectedRealtimePlatformModel(BaseModel):
    """
    Abstraction for the selected LLM realtime platform model.

    Allows to switch between Azure and OpenAI models from the configuration without changing the interface.
    """

    azure_openai: AzureOpenaiRealtimePlatformModel | None = None
    mode: ModeEnum
    openai: OpenaiRealtimePlatformModel | None = None

    @field_validator("azure_openai")
    @classmethod
    def _validate_azure_openai(
        cls,
        azure_openai: AzureOpenaiRealtimePlatformModel | None,
        info: ValidationInfo,
    ) -> AzureOpenaiRealtimePlatformModel | None:
        if not azure_openai and info.data.get("mode", None) == ModeEnum.AZURE_OPENAI:
            raise ValueError("Azure OpenAI config required")
        return azure_openai

    @field_validator("openai")
    @classmethod
    def _validate_openai(
        cls,
        openai: OpenaiRealtimePlatformModel | None,
        info: ValidationInfo,
    ) -> OpenaiRealtimePlatformModel | None:
        if not openai and info.data.get("mode", None) == ModeEnum.OPENAI:
            raise ValueError("OpenAI config required")
        return openai

    def selected(
        self,
    ) -> AbstractRealtimePlatformModel:
        platform = (
            self.azure_openai if self.mode == ModeEnum.AZURE_OPENAI else self.openai
        )
        assert platform
        return platform


class AbstractSequentialPlatformModel(AbstractPlatformModel):
    seed: int = 42  # Reproducible results
    streaming: bool
    temperature: float = 0.0  # Most focused and deterministic

    @abstractmethod
    async def instance(
        self,
    ) -> tuple[AsyncAzureOpenAI | AsyncOpenAI, "AbstractSequentialPlatformModel"]:
        pass


class AzureOpenaiSequentialPlatformModel(AbstractSequentialPlatformModel):
    """
    Properties for the sequential LLM models, like `gpt-4o-mini`, hosted on Azure.
    """

    _client: AsyncAzureOpenAI | None = None
    api_version: str = "2024-06-01"
    deployment: str
    endpoint: str

    async def instance(
        self,
    ) -> tuple[AsyncAzureOpenAI, "AzureOpenaiSequentialPlatformModel"]:
        if not self._client:
            self._client = AsyncAzureOpenAI(
                # Reliability
                max_retries=0,  # Retries are managed manually
                timeout=60,
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


class OpenaiSequentialPlatformModel(AbstractSequentialPlatformModel):
    """
    Properties for the sequential LLM models, like `gpt-4o-mini`, hosted on OpenAI.
    """

    _client: AsyncOpenAI | None = None
    api_key: SecretStr

    async def instance(
        self,
    ) -> tuple[AsyncOpenAI, "OpenaiSequentialPlatformModel"]:
        if not self._client:
            self._client = AsyncOpenAI(
                # Reliability
                max_retries=0,  # Retries are managed manually
                timeout=60,
                # Authentication
                api_key=self.api_key.get_secret_value(),
            )
        return self._client, self


class SelectedSequentialPlatformModel(BaseModel):
    """
    Abstraction for the selected LLM sequential platform model.

    Allows to switch between Azure and OpenAI models from the configuration without changing the interface.
    """

    azure_openai: AzureOpenaiSequentialPlatformModel | None = None
    mode: ModeEnum
    openai: OpenaiSequentialPlatformModel | None = None

    @field_validator("azure_openai")
    @classmethod
    def _validate_azure_openai(
        cls,
        azure_openai: AzureOpenaiSequentialPlatformModel | None,
        info: ValidationInfo,
    ) -> AzureOpenaiSequentialPlatformModel | None:
        if not azure_openai and info.data.get("mode", None) == ModeEnum.AZURE_OPENAI:
            raise ValueError("Azure OpenAI config required")
        return azure_openai

    @field_validator("openai")
    @classmethod
    def _validate_openai(
        cls,
        openai: OpenaiSequentialPlatformModel | None,
        info: ValidationInfo,
    ) -> OpenaiSequentialPlatformModel | None:
        if not openai and info.data.get("mode", None) == ModeEnum.OPENAI:
            raise ValueError("OpenAI config required")
        return openai

    def selected(
        self,
    ) -> AbstractSequentialPlatformModel:
        platform = (
            self.azure_openai if self.mode == ModeEnum.AZURE_OPENAI else self.openai
        )
        assert platform
        return platform


class LlmModel(BaseModel):
    """
    Properties for the LLM configuration.
    """

    realtime: SelectedRealtimePlatformModel
    sequential: SelectedSequentialPlatformModel
