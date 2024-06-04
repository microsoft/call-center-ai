from deepeval import assert_test
from deepeval.metrics import (
    AnswerRelevancyMetric,
    BiasMetric,
    ContextualRelevancyMetric,
    ToxicityMetric,
)
from deepeval.models.gpt_model import GPTModel
from deepeval.test_case import LLMTestCase
from helpers.call_events import (
    on_speech_recognized,
    on_call_connected,
)
from helpers.logging import logger
from models.call import CallStateModel
from models.reminder import ReminderModel
from models.training import TrainingModel
from pydantic import TypeAdapter
from pytest import assume
from tests.conftest import CallAutomationClientMock, with_conversations
import json
import pytest


@with_conversations
@pytest.mark.asyncio  # Allow async functions
@pytest.mark.repeat(3)  # Catch non deterministic issues
async def test_llm(
    call: CallStateModel,
    claim_tests_excl: list[str],
    claim_tests_incl: list[str],
    deepeval_model: GPTModel,
    expected_output: str,
    inputs: list[str],
    lang: str,
) -> None:
    """
    Test the LLM with a mocked conversation against the expected output.

    Steps:
    1. Run application with mocked inputs
    2. Combine all outputs
    3. Test claim data exists
    4. Test LLM metrics
    """

    def _play_media_callback(text: str) -> None:
        nonlocal actual_output
        actual_output += f" {text}"

    actual_output = ""

    # Mock client
    client = CallAutomationClientMock(play_media_callback=_play_media_callback)

    # Mock call
    call.lang = lang

    # Connect call
    await on_call_connected(
        call=call,
        client=client,
    )

    # Simulate conversation with speech recognition
    for input in inputs:
        await on_speech_recognized(
            call=call,
            client=client,
            post_callback=lambda _call: None,  # Disable post call
            text=input,
            trainings_callback=lambda _call: None,  # Disable training
        )

    # Remove newlines for log comparison
    actual_output = _remove_newlines(actual_output)
    full_input = _remove_newlines(" ".join(inputs))

    # Log for dev review
    logger.info(f"actual_output: {actual_output}")
    logger.info(f"claim: {call.claim}")
    logger.info(f"full_input: {full_input}")

    # Test claim data
    for field in claim_tests_incl:
        assume(call.claim.get(field, None), f"Claim field {field} is missing")

    # Configure LLM tests
    test_case = LLMTestCase(
        actual_output=actual_output,
        expected_output=expected_output,
        input=full_input,
        retrieval_context=[
            json.dumps(call.claim),
            TypeAdapter(list[ReminderModel]).dump_json(call.reminders).decode(),
            TypeAdapter(list[TrainingModel]).dump_json(await call.trainings()).decode(),
        ],
    )

    # Define LLM metrics
    llm_metrics = [
        BiasMetric(threshold=1, model=deepeval_model),
        ToxicityMetric(threshold=1, model=deepeval_model),
    ]  # By default, include generic metrics

    if not any(
        field == "answer_relevancy" for field in claim_tests_excl
    ):  # Test answer relevancy from questions
        llm_metrics.append(AnswerRelevancyMetric(threshold=0.5, model=deepeval_model))
    if not any(
        field == "contextual_relevancy" for field in claim_tests_excl
    ):  # Test answer relevancy from context
        llm_metrics.append(
            ContextualRelevancyMetric(threshold=0.25, model=deepeval_model)
        )

    # Execute LLM tests
    assert_test(test_case, llm_metrics)


def _remove_newlines(text: str) -> str:
    """
    Remove newlines from a string and return it as a single line.
    """
    return " ".join([line.strip() for line in text.splitlines()])
