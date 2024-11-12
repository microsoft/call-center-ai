import json

import pytest
from azure.ai.evaluation import AzureOpenAIModelConfiguration, QAEvaluator
from pydantic import TypeAdapter
from pytest_assume.plugin import assume

from app.helpers.call_events import (
    on_call_connected,
    on_call_disconnected,
    on_end_call,
    on_ivr_recognized,
    on_play_completed,
)
from app.helpers.logging import logger
from app.models.call import CallStateModel
from app.models.reminder import ReminderModel
from app.models.training import TrainingModel
from tests.conftest import CallAutomationClientMock, with_conversations


@with_conversations
@pytest.mark.asyncio(scope="session")
async def test_llm(  # noqa: PLR0913
    call: CallStateModel,
    claim_tests_excl: list[str],
    eval_config: AzureOpenAIModelConfiguration,
    expected_output: str,
    speeches: list[str],
    lang: str,
) -> None:
    """
    Test the LLM with a mocked conversation against the expected output.

    Steps:
    1. Run application with mocked speeches
    2. Combine all outputs
    3. Test claim data exists
    4. Test LLM metrics
    """

    def _play_media_callback(text: str) -> None:
        nonlocal actual_output
        actual_output += f" {text}"

    actual_output = ""

    # Mock client
    automation_client = CallAutomationClientMock(
        hang_up_callback=lambda: None,
        play_media_callback=_play_media_callback,
        transfer_callback=lambda: None,
    )
    call_client = automation_client.get_call_connection()

    # Mock call
    call.lang = lang

    async def _post_callback(_call: CallStateModel) -> None:
        await on_end_call(call=_call)

    # Connect call
    await on_call_connected(
        call=call,
        client=automation_client,
        server_call_id="dummy",
    )

    # First IVR
    await on_ivr_recognized(
        call=call,
        client=automation_client,
        label=call.lang.short_code,
    )

    # Simulate conversation with speech recognition
    for speech in speeches:
        # Respond
        await on_speech_recognized(
            call=call,
            client=automation_client,
            post_callback=_post_callback,
            text=speech,
        )
        # Receip
        await on_play_completed(
            call=call,
            client=automation_client,
            contexts=call_client.last_contexts,
            post_callback=_post_callback,
        )
        # Reset contexts
        call_client.last_contexts.clear()

    # Disconnect call
    await on_call_disconnected(
        call=call,
        client=automation_client,
        post_callback=_post_callback,
    )

    # Remove newlines for log comparison
    actual_output = _remove_newlines(actual_output)
    full_speech = _remove_newlines(" ".join(speeches))

    # Log for dev review
    logger.info("actual_output: %s", actual_output)
    logger.info("claim: %s", call.claim)
    logger.info("full_speech: %s", full_speech)

    assume(call.next, "No next action found")
    assume(call.synthesis, "No synthesis found")

    qa_eval = QAEvaluator(
        model_config=eval_config,
    )

    qa_res = qa_eval(
        ground_truth=expected_output,
        query=full_speech,
        response=actual_output,
        context="\n".join(
            [
                json.dumps(call.claim),
                TypeAdapter(list[ReminderModel]).dump_json(call.reminders).decode(),
                TypeAdapter(list[TrainingModel])
                .dump_json(await call.trainings())
                .decode(),
            ]
        ),
    )

    kpis = {"groundedness", "relevance", "coherence", "fluency", "similarity"}

    if not any(
        field == "answer_relevancy" for field in claim_tests_excl
    ):  # Test respond relevancy from questions
        kpis.remove("relevance")
    if not any(
        field == "contextual_relevancy" for field in claim_tests_excl
    ):  # Test respond relevancy from context
        kpis.remove("coherence")

    for kpi in ("groundedness", "relevance", "coherence", "fluency", "similarity"):
        assume(
            qa_res["kpi"] >= 1.5,
            f"KPI {kpi} is below threshold: {qa_res['kpi']}",
        )


def _remove_newlines(text: str) -> str:
    """
    Remove newlines from a string and return it as a single line.
    """
    return " ".join([line.strip() for line in text.splitlines()])
