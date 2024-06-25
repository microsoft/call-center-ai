from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from enum import Enum
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic import field_validator, SecretStr, BaseModel, ValidationInfo, Field
from typing import Any, Optional, Union


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
    streaming: bool


class AzureOpenaiPlatformModel(AbstractPlatformModel):
    _client: Optional[AsyncAzureOpenAI] = None
    api_key: Optional[SecretStr] = None
    deployment: str
    endpoint: str

    def instance(self) -> tuple[AsyncAzureOpenAI, AbstractPlatformModel]:
        if not self._client:
            api_key = self.api_key.get_secret_value() if self.api_key else None
            token_func = (
                get_bearer_token_provider(
                    ManagedIdentityCredential(),
                    "https://cognitiveservices.azure.com/.default",
                )
                if not self.api_key
                else None
            )
            self._client = AsyncAzureOpenAI(
                **self._client_kwargs,
                # Deployment
                api_version="2023-12-01-preview",
                azure_deployment=self.deployment,
                azure_endpoint=self.endpoint,
                # Authentication
                api_key=api_key,
                azure_ad_token_provider=token_func,
            )
        return self._client, self


class OpenaiPlatformModel(AbstractPlatformModel):
    _client: Optional[AsyncOpenAI] = None
    api_key: SecretStr
    endpoint: str

    def instance(self) -> tuple[AsyncOpenAI, AbstractPlatformModel]:
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
    azure_openai: Optional[AzureOpenaiPlatformModel] = None
    mode: ModeEnum
    openai: Optional[OpenaiPlatformModel] = None

    @field_validator("azure_openai")
    def _validate_azure_openai(
        cls,
        azure_openai: Optional[AzureOpenaiPlatformModel],
        info: ValidationInfo,
    ) -> Optional[AzureOpenaiPlatformModel]:
        if not azure_openai and info.data.get("mode", None) == ModeEnum.AZURE_OPENAI:
            raise ValueError("Azure OpenAI config required")
        return azure_openai

    @field_validator("openai")
    def _validate_openai(
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
    fast: SelectedPlatformModel = Field(
        serialization_alias="backup",  # Backwards compatibility with v6
    )
    slow: SelectedPlatformModel = Field(
        serialization_alias="primary",  # Backwards compatibility with v6
    )

    def selected(
        self, is_fast: bool
    ) -> Union[AzureOpenaiPlatformModel, OpenaiPlatformModel]:
        platform = self.fast if is_fast else self.slow
        return platform.selected()
