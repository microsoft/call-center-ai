import asyncio
import re

import pytest
from deepeval import assert_test
from deepeval.metrics import BaseMetric
from deepeval.models.gpt_model import GPTModel
from deepeval.test_case import LLMTestCase
from pydantic import TypeAdapter
from pytest_assume.plugin import assume

from app.helpers.config import CONFIG
from app.helpers.logging import logger
from app.models.call import CallStateModel
from app.models.message import MessageModel, PersonaEnum as MessagePersonaEnum
from app.models.training import TrainingModel
from tests.conftest import with_conversations


class RagRelevancyMetric(BaseMetric):
    model: GPTModel

    def __init__(
        self,
        model: GPTModel,
        threshold: float = 0.5,
    ):
        self.threshold = threshold
        self.model = model

    def measure(
        self,
        *args,
        **kwargs,
    ):
        raise NotImplementedError("Use a_measure instead")

    async def a_measure(
        self,
        test_case: LLMTestCase,
        *args,  # noqa: ARG002
        **kwargs,  # noqa: ARG002
    ) -> float:
        assert test_case.input
        assert test_case.retrieval_context
        # Measure each document in parallel
        scores = await asyncio.gather(
            *[
                self._measure_single(message=test_case.input, document=document)
                for document in test_case.retrieval_context
            ]
        )
        logger.info("Scores: %s", scores)
        # Res 1 is weighted 1, res 2 is weighted 0.5, res 3 is weighted 0.33, ...
        weights = [score / i for i, score in enumerate(scores, start=1)]
        # Score is the weighted average, top 1 result should be the most important
        self.score = sum(x * y for x, y in zip(scores, weights)) / sum(weights)
        # Test against the threshold
        self.success = self.score >= self.threshold
        return self.score

    async def _measure_single(self, document: str, message: str) -> float:
        score = 0
        res, _ = await self.model.a_generate(
            f"""
            Assistant is a data analyst expert with 20 years of experience.

            # Objective
            Analyze a document and decide whether it would be useful to respond to the user message.

            # Context
            The document comes from a database. It has been stemmed. It may contains technical data and jargon a specialist would use.

            # Rules
            - Respond only with the float value, never add other text
            - Response 0.0 means not useful at all, 1.0 means totally useful

            # Message
            {message}

            # Document
            {document}

            # Response format
            score, float between 0.0 and 1.0

            ## Example 1
            Message: I love bananas
            Document: bananas are yellow, apples are red
            Assistant: 1.0

            ## Example 2
            Message: The sky is blue
            Document: mouse is a rodent, mouse is a computer peripheral
            Assistant: 0.0

            ## Example 3
            Message: my car is stuck in the mud
            Document: car accidents must be reported within 24 hours, car accidents are dangerous
            Assistant: 0.7
        """
        )
        try:
            score = float(res)
        except ValueError as e:
            group = re.search(r"\d+\.\d+", res)
            if group:
                return float(group.group())
            raise ValueError(f"LLM response is not a number: {res}") from e
        return score

    def is_successful(self) -> bool:
        return self.success or False

    @property
    def __name__(self):  # pyright: ignore
        return "RAG Relevancy"


@with_conversations
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.repeat(10)  # Catch multi-threading and concurrency issues
async def test_relevancy(  # noqa: PLR0913
    call: CallStateModel,
    claim_tests_excl: list[str],  # noqa: ARG001
    deepeval_model: GPTModel,
    expected_output: str,  # noqa: ARG001
    speeches: list[str],
    lang: str,
) -> None:
    """
    Test the relevancy of the Message to the training data.

    Steps:
    1. Search for training data
    2. Ask the LLM for relevancy score
    3. Assert the relevancy score is above 0.5

    Test is repeated 10 times to catch multi-threading and concurrency issues.
    """
    # Set call language
    call.lang_short_code = lang

    # Fill call with messages
    for speech in speeches:
        call.messages.append(
            MessageModel(
                content=speech,
                lang_short_code=call.lang.short_code,
                persona=MessagePersonaEnum.HUMAN,
            )
        )

    # Get trainings
    trainings = await call.trainings(cache_only=False)

    logger.info("Messages: %s", call.messages)
    logger.info("Trainings: %s", trainings)

    if not trainings:
        logger.warning("No training data found, please add objects in AI Search")
        return

    # Confirm top N config is respected
    len_trainings = len(trainings)
    max_documents = (
        CONFIG.ai_search.top_n_documents * CONFIG.ai_search.expansion_n_messages
    )
    assume(
        len_trainings <= max_documents,
        f"Data is too large, should be max {max_documents}, actual is {len_trainings}",
    )

    # Confirm strictness config is respected
    min_score = CONFIG.ai_search.strictness
    for training in trainings or []:
        actual_score = training.score
        assume(
            actual_score >= min_score,
            f"Model score is too low, should be min {min_score}, actual is {actual_score}",
        )

    # Configure LLM tests
    full_speech = " ".join([message.content for message in call.messages])
    test_case = LLMTestCase(
        actual_output="",  # Not used
        input=full_speech,
        retrieval_context=[
            TypeAdapter(TrainingModel)
            .dump_json(training, exclude=TrainingModel.excluded_fields_for_llm())
            .decode()
            for training in trainings
        ],
    )

    # Define LLM metrics
    llm_metrics: list[BaseMetric] = [
        RagRelevancyMetric(
            threshold=0.5, model=deepeval_model
        ),  # Compare speech to the retrieval context
    ]

    # Execute LLM tests
    assert_test(test_case, llm_metrics)
