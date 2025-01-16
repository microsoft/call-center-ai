import hashlib
import random
import string
import xml.etree.ElementTree as ET
from collections.abc import Callable
from textwrap import dedent
from typing import Any

import pytest
import pytest_asyncio
import yaml
from _pytest.mark.structures import MarkDecorator
from azure.cognitiveservices.speech import (
    ResultFuture,
    SpeechSynthesisResult,
    SpeechSynthesizer,
)
from azure.cognitiveservices.speech.interop import _spx_handle
from azure.communication.callautomation import FileSource, SsmlSource, TextSource
from azure.communication.callautomation._generated.aio.operations import (
    CallMediaOperations,
    CallRecordingOperations,
)
from azure.communication.callautomation._generated.models import RecordingStateResponse
from azure.communication.callautomation._models import TransferCallResult
from azure.communication.callautomation.aio import (
    CallAutomationClient,
    CallConnectionClient,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from deepeval.models.gpt_model import GPTModel
from langchain_openai import AzureChatOpenAI
from pydantic import BaseModel, ValidationError

from app.helpers.call_utils import ContextEnum as CallContextEnum
from app.helpers.config import CONFIG
from app.helpers.logging import logger
from app.main import _str_to_contexts
from app.models.call import CallInitiateModel, CallStateModel


class CallMediaOperationsMock(CallMediaOperations):
    def __init__(self) -> None:
        pass

    async def start_media_streaming(
        self,
        *args,
        **kwargs,
    ) -> None:
        pass


class CallRecordingOperationsMock(CallRecordingOperations):
    def __init__(self) -> None:
        pass

    async def start_recording(
        self,
        *args,  # noqa: ARG002
        **kwargs,  # noqa: ARG002
    ) -> RecordingStateResponse:
        return RecordingStateResponse()


class CallConnectionClientMock(CallConnectionClient):
    _call_connection_id: str = "dummy"
    _call_media_client = CallMediaOperationsMock()
    _hang_up_callback: Callable[[], None]
    _play_media_callback: Callable[[str], None]
    _transfer_callback: Callable[[], None]

    last_contexts: set[CallContextEnum] = set()

    def __init__(
        self,
        hang_up_callback: Callable[[], None],
        play_media_callback: Callable[[str], None],
        transfer_callback: Callable[[], None],
    ) -> None:
        self._hang_up_callback = hang_up_callback
        self._play_media_callback = play_media_callback
        self._transfer_callback = transfer_callback

    async def start_recognizing_media(
        self,
        play_prompt: FileSource | TextSource | SsmlSource,
        *args,  # noqa: ARG002
        operation_context: str | None = None,
        **kwargs,  # noqa: ARG002
    ) -> None:
        contexts = _str_to_contexts(operation_context)
        for context in contexts or []:
            self.last_contexts.add(context)
        self._log_media(play_prompt)

    async def play_media(
        self,
        play_source: FileSource | TextSource | SsmlSource,
        *args,  # noqa: ARG002
        operation_context: str | None = None,
        **kwargs,  # noqa: ARG002
    ) -> None:
        contexts = _str_to_contexts(operation_context)
        for context in contexts or []:
            self.last_contexts.add(context)
        self._log_media(play_source)

    async def transfer_call_to_participant(
        self,
        *args,  # noqa: ARG002
        **kwargs,  # noqa: ARG002
    ) -> TransferCallResult:
        self._transfer_callback()
        return TransferCallResult()

    async def hang_up(
        self,
        *args,  # noqa: ARG002
        **kwargs,  # noqa: ARG002
    ) -> None:
        self._hang_up_callback()

    async def cancel_all_media_operations(
        self,
        *args,
        **kwargs,
    ) -> None:
        pass

    def _log_media(self, play_source: FileSource | TextSource | SsmlSource) -> None:
        if isinstance(play_source, TextSource):
            self._play_media_callback(play_source.text.strip())
        elif isinstance(play_source, SsmlSource):
            # deepcode ignore InsecureXmlParser/test: SSML is internally generated
            for text in ET.fromstring(play_source.ssml_text).itertext():
                if text.strip():
                    self._play_media_callback(text.strip())


class CallAutomationClientMock(CallAutomationClient):
    _call_client: CallConnectionClientMock
    _call_recording_client = CallRecordingOperationsMock()

    def __init__(
        self,
        hang_up_callback: Callable[[], None],
        play_media_callback: Callable[[str], None],
        transfer_callback: Callable[[], None],
    ) -> None:
        self._call_client = CallConnectionClientMock(
            hang_up_callback=hang_up_callback,
            play_media_callback=play_media_callback,
            transfer_callback=transfer_callback,
        )

    def get_call_connection(
        self,
        *args,  # noqa: ARG002
        **kwargs,  # noqa: ARG002
    ) -> CallConnectionClientMock:
        return self._call_client


class SpeechSynthesizerMock(SpeechSynthesizer):
    _play_media_callback: Callable[[str], None]

    def __init__(self, play_media_callback: Callable[[str], None]) -> None:
        self._play_media_callback = play_media_callback

    def speak_ssml_async(
        self,
        ssml: str,
        *args,  # noqa: ARG002
        **kwargs,  # noqa: ARG002
    ) -> ResultFuture:
        self._play_media_callback(ssml)
        return ResultFuture(
            async_handle=_spx_handle(0),
            get_function=lambda _: _spx_handle(0),
            wrapped_type=SpeechSynthesisResult,
        )


class DeepEvalAzureOpenAI(GPTModel):
    _cache: pytest.Cache
    _langchain_kwargs: dict[str, Any]
    _model: AzureChatOpenAI

    def __init__(
        self,
        cache: pytest.Cache,
        **kwargs,
    ):
        platform = CONFIG.llm.fast
        assert platform

        _langchain_kwargs = {
            # Repeatability
            "model_kwargs": {
                "seed": platform.seed,
            },
            "temperature": platform.temperature,
            # Deployment
            "api_version": platform.api_version,
            "azure_endpoint": platform.endpoint,
            "model": platform.model,
            # Authentication, either RBAC or API
            "azure_ad_token_provider": get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            ),
            # DeepEval
            **kwargs,
        }
        self._cache = cache
        self._model = AzureChatOpenAI(**_langchain_kwargs)

    def generate(self, prompt: str) -> tuple[str, float]:
        prompt = dedent(prompt).strip()
        cache_key = self._cache_key(prompt)
        # Try cache
        content: tuple[str, float] = self._cache.get(cache_key, None)
        if content:
            return content
        # Try live
        res = super().generate(prompt)
        # Update cache
        self._cache.set(
            key=cache_key,
            value=res,
        )
        # Return
        return res

    async def a_generate(self, prompt: str) -> tuple[str, float]:
        prompt = dedent(prompt).strip()
        cache_key = self._cache_key(prompt)
        # Try cache
        content: tuple[str, float] = self._cache.get(cache_key, None)
        if content:
            return content
        # Try live
        res = await super().a_generate(prompt)
        # Update cache
        self._cache.set(
            key=cache_key,
            value=res,
        )
        # Return
        return res

    def get_model_name(self) -> str:
        return "Azure OpenAI"

    def load_model(self) -> AzureChatOpenAI:
        return self._model

    def should_use_azure_openai(self) -> bool:
        return True

    def _cache_key(self, prompt: str) -> str:
        llm_string = self._model._get_llm_string(input=prompt)
        llm_hash = hashlib.sha256(llm_string.encode(), usedforsecurity=False).digest()
        return f"call-center-ai/{llm_hash}"


class Conversation(BaseModel):
    claim_tests_excl: list[str] = []
    expected_output: str
    id: str
    speeches: list[str]
    lang: str


def with_conversations(fn=None) -> MarkDecorator:
    with open(
        encoding="utf-8",
        file="tests/conversations.yaml",
    ) as f:
        file: dict = yaml.safe_load(f)
    conversations: list[Conversation] = []
    for conv in file.get("conversations", []):
        try:
            conversations.append(Conversation.model_validate(conv))
        except ValidationError:
            logger.exception("Failed to parse conversation")
    print(f"Loaded {len(conversations)} conversations")  # noqa: T201
    keys = sorted(Conversation.model_fields.keys() - {"id"})
    values = [
        pytest.param(
            *[conversation.model_dump()[key] for key in keys],
            id=conversation.id,
        )
        for conversation in conversations
    ]
    decorator = pytest.mark.parametrize(keys, values)
    if fn:
        return decorator(fn)
    return decorator


@pytest.fixture
def random_text() -> str:
    text = "".join(random.choice(string.printable) for _ in range(100))
    return text


@pytest_asyncio.fixture
async def call() -> CallStateModel:
    db = CONFIG.database.instance
    call = await db.call_create(
        CallStateModel(
            initiate=CallInitiateModel(
                **CONFIG.conversation.initiate.model_dump(),
                phone_number="+33612345678",  # pyright: ignore
            ),
            voice_id="dummy",
        )
    )
    return call


@pytest.fixture
def deepeval_model(cache: pytest.Cache) -> GPTModel:
    return DeepEvalAzureOpenAI(cache)
