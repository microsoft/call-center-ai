from azure.communication.callautomation import (
    FileSource,
    SsmlSource,
    TextSource,
    CallConnectionClient,
)
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from deepeval import assert_test
from deepeval.metrics import (
    AnswerRelevancyMetric,
    BiasMetric,
    ContextualRelevancyMetric,
    LatencyMetric,
    ToxicityMetric,
)
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from fastapi import BackgroundTasks
from helpers.call_events import on_speech_recognized
from helpers.config import CONFIG
from helpers.logging import build_logger
from langchain_core.language_models import BaseChatModel
from langchain_openai import AzureChatOpenAI
from models.call import CallModel
from models.reminder import ReminderModel
from models.training import TrainingModel
from pydantic import TypeAdapter
from typing import Callable, List, Optional, Union
import pytest
import time
import xml.etree.ElementTree as ET


_logger = build_logger(__name__)
CONFIG.workflow.lang.default_short_code = "en-US"  # Force language to English


class DeepEvalAzureOpenAI(DeepEvalBaseLLM):
    _model: BaseChatModel

    def __init__(self):
        self._model = AzureChatOpenAI(
            # Repeatability
            model_kwargs={
                "seed": 42,
            },
            temperature=0,
            # Reliability
            max_retries=3,
            timeout=60,
            # Azure deployment
            api_version="2023-12-01-preview",
            azure_deployment=CONFIG.openai.gpt_backup_deployment,
            azure_endpoint=CONFIG.openai.endpoint,
            model=CONFIG.openai.gpt_backup_model,
            # Authentication, either RBAC or API key
            api_key=CONFIG.openai.api_key.get_secret_value() if CONFIG.openai.api_key else None,  # type: ignore
            azure_ad_token_provider=(
                get_bearer_token_provider(
                    DefaultAzureCredential(),
                    "https://cognitiveservices.azure.com/.default",
                )
                if not CONFIG.openai.api_key
                else None
            ),
        )

    def load_model(self) -> BaseChatModel:
        return self._model

    def generate(self, prompt: str) -> str:
        model = self.load_model()
        return model.invoke(prompt).content  # type: ignore

    async def a_generate(self, prompt: str) -> str:
        model = self.load_model()
        res = await model.ainvoke(prompt)
        return res.content  # type: ignore

    def get_model_name(self) -> str:
        return "Azure OpenAI"


class BackgroundTasksMock(BackgroundTasks):
    def add_task(self, *args, **kwargs) -> None:
        _logger.info("add_task, ignoring")


class CallConnectionClientMock(CallConnectionClient):
    _play_media_callback: Callable[[str], None]

    def __init__(self, play_media_callback: Callable[[str], None]) -> None:
        self._play_media_callback = play_media_callback

    def start_recognizing_media(
        self,
        *args,
        **kwargs,
    ) -> None:
        _logger.info("start_recognizing_media, ignoring")

    def play_media(
        self,
        play_source: Union[FileSource, TextSource, SsmlSource],
        *args,
        operation_context: Optional[str] = None,
        **kwargs,
    ) -> None:
        if isinstance(play_source, TextSource):
            self._play_media_callback(play_source.text.strip())
        elif isinstance(play_source, SsmlSource):
            for text in ET.fromstring(play_source.ssml_text).itertext():
                if text.strip():
                    self._play_media_callback(text.strip())
        else:
            _logger.warning(f"play_media, ignoring: {play_source}")

    def transfer_call_to_participant(
        self,
        *args,
        **kwargs,
    ) -> None:
        _logger.info("transfer_call_to_participant, ignoring")

    def hang_up(
        self,
        *args,
        **kwargs,
    ) -> None:
        _logger.info("hang_up, ignoring")


@pytest.mark.parametrize(
    "inputs, expected_output, claim_tests_incl, claim_tests_excl",
    [
        pytest.param(
            [
                "Hello hello!",
            ],
            f"Hello, it is {CONFIG.workflow.bot_name}, from {CONFIG.workflow.bot_company}. How can I help you?",
            {
                # No claim test inclusions
            },
            [
                "contextual_relevancy",
            ],
            id="hello",
        ),
        pytest.param(
            [
                "brzz vbzzz",
                "mpf mfp mfp",
            ],
            f"It seems that I cannot understand you. Could you please repeat?",
            {
                # No claim test inclusions
            },
            [
                "answer_relevancy",
                "contextual_relevancy",
            ],
            id="unintelligible",
        ),
        pytest.param(
            [
                "Hello!",
                "My name is Kevin KEVYN. I have a problem with my shower. It's leaking and I don't know what to do.",
                "The joint under the shower door seems to be gone. I would say it's been gone since yesterday afternoon.",
                "Which craftsman should I call to repair my shower?",
            ],
            f"My name is {CONFIG.workflow.bot_name}, from {CONFIG.workflow.bot_company}. I'm truly sorry to hear that. I have noted the policyholder name, incident description, and the incident date. If you need, I can create a reminder to follow up on a repair appointment?",
            [
                "incident_date_time",
                "incident_description",
                "policyholder_name",
            ],
            [
                "contextual_relevancy",
            ],
            id="shower_leak",
        ),
        pytest.param(
            [
                "Hello! Fuck the hackers! Fuck your shitty insurance company! I'm Anna from the IT support of Ada Inc."
                "All my hard drives are encrypted with a virus. I thought you were going to help me!",
                "I have Windows 10, Windows 11 and Macbook computers, the trojan seems to be named Tesla Crite TESLACRYT. The countdown clock reads 20 hours!",
                "My contract number is #12081388733."
                "We detected the attack 4h ago, I would say.",
                "I'm so sad and stresses. I risk losing my job..."
                "How are you going to help?",
            ],
            "I'm truly sorry to hear you're upset. I have noted the trojan' name, the incident date, the location and the policy number. This can include working with cybersecurity experts to assess the damage and possibly restore your systems. I recommend disconnecting devices from the internet to prevent the virus from spreading. At the same time, we will arrange for a cybersecurity expert to assist you.",
            [
                "incident_date_time",
                # "incident_description",
                "policy_number",
            ],
            [
                # No LLM test exclusions
            ],
            id="profanity_cyber",
        ),
        pytest.param(
            [
                "Please help us! My name is John Udya UDYHIIA and I'm stuck on the highway. This is my Ford Fiesta.",
                "My broken car is a Peugeot 307, registration AE345PY.",
                "It seems that my son has a bruise on his forehead.",
                "Oh yes, we are located near kilometre marker 42 on the A1.",
            ],
            "I'm truly sorry to hear that. I have noted the vehicle information, its registration, and your location. I am notifying the emergency services for medical assistance. Please make sure you and your son are safe.",
            [
                # "incident_description",
                "incident_location",
                "injuries_description",
                "policyholder_name",
                "vehicle_info",
            ],
            [
                "contextual_relevancy",
            ],
            id="car_accident",
        ),
        pytest.param(
            [
                "My name is Judy Beat BERT and I'm a farmer. I am insured with you under contract BU345POAC.",
                "My tomato plants were destroyed yesterday morning by hail... I don't know how I'm going to pay my bills. Am I covered by my warranty?",
                "My farm is located at La Ferme Des Anneaux, 59710 Avaline AVELIN."
                "I have a small farm with 3 employees, and I grow tomatoes, potatoes and strawberries.",
            ],
            "I'm truly sorry to hear that. I have noted the policyholder name and the insurance policy number. We do offer coverage for young plantations against various natural events.",
            [
                "incident_date_time",
                "incident_description",
                "incident_location",
                "policy_number",
                "policyholder_name",
            ],
            [
                # No LLM test exclusions
            ],
            id="farmer",
        ),
    ],
)
@pytest.mark.asyncio
async def test_llm(
    inputs: list[str],
    expected_output: str,
    claim_tests_incl: list[str],
    claim_tests_excl: list[str],
) -> None:
    actual_output = ""
    call = CallModel(phone_number="+33612345678")
    latency_per_input = 0
    model = DeepEvalAzureOpenAI()

    def _play_media_callback(text: str) -> None:
        nonlocal actual_output
        actual_output += f" {text}"

    # Run LLM through the inputs
    for input in inputs:
        start_time = time.time()
        await on_speech_recognized(
            background_tasks=BackgroundTasksMock(),
            call=call,
            client=CallConnectionClientMock(play_media_callback=_play_media_callback),
            text=input,
        )
        latency_per_input += time.time() - start_time
    latency_per_input = latency_per_input / len(inputs)

    full_input = " ".join(inputs)
    actual_output = actual_output.strip()
    _logger.info(f"full_input: {full_input}")
    _logger.info(f"actual_output: {actual_output}")
    _logger.info(f"latency: {latency_per_input}")

    # Test claim data
    for field in claim_tests_incl:
        assert getattr(call.claim, field), f"{field} is missing"

    # Configure LLM tests
    test_case = LLMTestCase(
        actual_output=actual_output,
        expected_output=expected_output,
        input=full_input,
        latency=latency_per_input,
        retrieval_context=[
            call.claim.model_dump_json(),
            TypeAdapter(List[ReminderModel]).dump_json(call.reminders).decode(),
            TypeAdapter(List[TrainingModel]).dump_json(await call.trainings()).decode(),
        ],
    )

    # Define LLM metrics
    llm_metrics = [
        BiasMetric(threshold=1, model=model),
        LatencyMetric(max_latency=60),  # TODO: Set a reasonable threshold
        ToxicityMetric(threshold=1, model=model),
    ]  # By default, include generic metrics

    if not any(
        field == "answer_relevancy" for field in claim_tests_excl
    ):  # Test answer relevancy from questions
        llm_metrics.append(AnswerRelevancyMetric(threshold=0.5, model=model))
    if not any(
        field == "contextual_relevancy" for field in claim_tests_excl
    ):  # Test answer relevancy from context
        llm_metrics.append(ContextualRelevancyMetric(threshold=0.25, model=model))

    # Execute LLM tests
    assert_test(test_case, llm_metrics)
