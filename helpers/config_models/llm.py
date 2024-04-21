from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from contextlib import asynccontextmanager
from enum import Enum
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import field_validator, SecretStr, BaseModel, ValidationInfo
from typing import Any, AsyncGenerator, Optional, Tuple, Union


class ModeEnum(str, Enum):
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"


class AbstractPlatformModel(BaseModel, frozen=True):
    _client_kwargs: dict[str, Any] = {
        # Reliability
        "max_retries": 3,
        "timeout": 60,
    }
    context: int
    model: str
    streaming: bool


class AzureOpenaiPlatformModel(AbstractPlatformModel, frozen=True):
    api_key: Optional[SecretStr] = None
    deployment: str
    endpoint: str

    @asynccontextmanager
    async def instance(
        self,
    ) -> AsyncGenerator[Tuple[AsyncAzureOpenAI, AbstractPlatformModel], None]:
        api_key = self.api_key.get_secret_value() if self.api_key else None
        token_func = (
            get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
            if not self.api_key
            else None
        )
        client = AsyncAzureOpenAI(
            **self._client_kwargs,
            # Azure deployment
            api_version="2023-12-01-preview",
            azure_deployment=self.deployment,
            azure_endpoint=self.endpoint,
            # Authentication, either RBAC or API key
            api_key=api_key,
            azure_ad_token_provider=token_func,
        )
        try:
            yield client, self
        finally:
            await client.close()


class OpenaiPlatformModel(AbstractPlatformModel, frozen=True):
    api_key: SecretStr
    endpoint: str

    @asynccontextmanager
    async def instance(
        self,
    ) -> AsyncGenerator[Tuple[AsyncOpenAI, AbstractPlatformModel], None]:
        client = AsyncOpenAI(
            **self._client_kwargs,
            # OpenAI deployment
            base_url=self.endpoint,
            # Authentication, either API key or no auth
            api_key=self.api_key.get_secret_value(),
        )
        try:
            yield client, self
        finally:
            await client.close()


class SelectedPlatformModel(BaseModel, frozen=True):
    azure_openai: Optional[AzureOpenaiPlatformModel] = None
    mode: ModeEnum
    openai: Optional[OpenaiPlatformModel] = None

    @field_validator("azure_openai")
    def validate_azure_openai(
        cls,
        azure_openai: Optional[AzureOpenaiPlatformModel],
        info: ValidationInfo,
    ) -> Optional[AzureOpenaiPlatformModel]:
        if not azure_openai and info.data.get("mode", None) == ModeEnum.AZURE_OPENAI:
            raise ValueError("Azure OpenAI config required")
        return azure_openai

    @field_validator("openai")
    def validate_openai(
        cls,
        openai: Optional[OpenaiPlatformModel],
        info: ValidationInfo,
    ) -> Optional[OpenaiPlatformModel]:
        if not openai and info.data.get("mode", None) == ModeEnum.OPENAI:
            raise ValueError("OpenAI config required")
        return openai

    def selected(self) -> Union[AzureOpenaiPlatformModel, OpenaiPlatformModel]:
        platform = (
            self.azure_openai if self.mode == ModeEnum.AZURE_OPENAI else self.openai
        )
        assert platform
        return platform


class LlmModel(BaseModel):
    backup: SelectedPlatformModel
    primary: SelectedPlatformModel

    def selected(
        self, is_backup: bool
    ) -> Union[AzureOpenaiPlatformModel, OpenaiPlatformModel]:
        platform = self.backup if is_backup else self.primary
        return platform.selected()
