from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from deepeval.models.gpt_model import GPTModel
from fastapi import BackgroundTasks
from helpers.config import CONFIG
from helpers.logging import logger
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI
from models.call import CallStateModel, CallInitiateModel
from textwrap import dedent
from typing import Any, Callable, Optional, Tuple, Union
import hashlib
import pytest
import random
import string
import xml.etree.ElementTree as ET
from azure.communication.callautomation import (
    CallAutomationClient,
    CallConnectionClient,
    FileSource,
    SsmlSource,
    TextSource,
)


class CallAutomationClientMock(CallAutomationClient):
    _play_media_callback: Callable[[str], None]

    def __init__(self, play_media_callback: Callable[[str], None]) -> None:
        self._play_media_callback = play_media_callback

    def get_call_connection(
        self,
        *args,
        **kwargs,
    ) -> CallConnectionClient:
        return CallConnectionClientMock(self._play_media_callback)


class CallConnectionClientMock(CallConnectionClient):
    _play_media_callback: Callable[[str], None]

    def __init__(self, play_media_callback: Callable[[str], None]) -> None:
        self._play_media_callback = play_media_callback

    def start_recognizing_media(
        self,
        *args,
        **kwargs,
    ) -> None:
        logger.info("start_recognizing_media, ignoring")

    def play_media(
        self,
        play_source: Union[FileSource, TextSource, SsmlSource],
        *args,
        **kwargs,
    ) -> None:
        if isinstance(play_source, TextSource):
            self._play_media_callback(play_source.text.strip())
        elif isinstance(play_source, SsmlSource):
            for text in ET.fromstring(play_source.ssml_text).itertext():
                if text.strip():
                    self._play_media_callback(text.strip())
        else:
            logger.warning("play_media, ignoring")

    def transfer_call_to_participant(
        self,
        *args,
        **kwargs,
    ) -> None:
        logger.info("transfer_call_to_participant, ignoring")

    def hang_up(
        self,
        *args,
        **kwargs,
    ) -> None:
        logger.info("hang_up, ignoring")


@pytest.fixture
def background_tasks() -> BackgroundTasks:
    return BackgroundTasks()


@pytest.fixture
def random_text() -> str:
    text = "".join(random.choice(string.printable) for _ in range(100))
    return text


@pytest.fixture
def call() -> CallStateModel:
    call = CallStateModel(
        initiate=CallInitiateModel(
            **CONFIG.workflow.initiate.model_dump(),
            phone_number="+33612345678",  # type: ignore
        ),
        voice_id="dummy",
    )
    return call


@pytest.fixture
def deepeval_model(cache: pytest.Cache) -> GPTModel:
    return DeepEvalAzureOpenAI(cache)


class DeepEvalAzureOpenAI(GPTModel):
    _cache: pytest.Cache
    _langchain_kwargs: dict[str, Any]
    _model: BaseChatModel

    def __init__(
        self,
        cache: pytest.Cache,
        **kwargs,
    ):
        platform = CONFIG.llm.backup.azure_openai
        assert platform

        _langchain_kwargs = {
            # Repeatability
            "model_kwargs": {
                "seed": 42,
            },
            "temperature": 0,
            # Azure deployment
            "api_version": "2023-12-01-preview",
            "azure_deployment": platform.deployment,
            "azure_endpoint": platform.endpoint,
            "model": platform.model,
            # Authentication, either RBAC or API
            "api_key": platform.api_key.get_secret_value() if platform.api_key else None,  # type: ignore
            "azure_ad_token_provider": (
                get_bearer_token_provider(
                    ManagedIdentityCredential(),
                    "https://cognitiveservices.azure.com/.default",
                )
                if not platform.api_key
                else None
            ),
            # DeepEval
            **kwargs,
        }
        self._cache = cache
        self._model = AzureChatOpenAI(**_langchain_kwargs)

    def generate(self, *args, **kwargs):
        raise NotImplementedError

    async def a_generate(self, prompt: str) -> Tuple[str, float]:
        prompt = dedent(prompt).strip()
        cache_key = self._cache_key(prompt)
        # Try cache
        content: Tuple[str, float] = self._cache.get(cache_key, None)
        if content:
            return content
        # Try live
        res = await super().a_generate(prompt)
        # Update cache
        self._cache.set(cache_key, res)
        return res

    def get_model_name(self) -> str:
        return "Azure OpenAI"

    def load_model(self) -> BaseChatModel:
        return self._model

    def should_use_azure_openai(self) -> bool:
        return True

    def _cache_key(self, prompt: str) -> str:
        llm_string = self._model._get_llm_string(input=prompt)
        llm_hash = hashlib.sha256(llm_string.encode(), usedforsecurity=False).digest()
        return f"call-center-ai/{llm_hash}"
