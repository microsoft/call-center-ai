from deepeval import assert_test
from deepeval.metrics import BaseMetric
from deepeval.models.gpt_model import GPTModel
from deepeval.test_case import LLMTestCase
from helpers.config import CONFIG
from helpers.logging import logger
from models.call import CallStateModel
from models.message import MessageModel, PersonaEnum as MessagePersonaEnum
from models.training import TrainingModel
from pydantic import TypeAdapter
from pytest import assume
from tests.conftest import with_conversations
from typing import Optional
import asyncio
import pytest
import re


class RagRelevancyMetric(BaseMetric):
    model: GPTModel
    threshold: float

    def __init__(
        self,
        model: GPTModel,
        threshold: float = 0.5,
    ):
        self.threshold = threshold
        self.model = model

    async def a_measure(
        self,
        test_case: LLMTestCase,
        *args,
        **kwargs,
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
        logger.info(f"Scores: {scores}")
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
        except ValueError:
            group = re.search(r"\d+\.\d+", res)
            if group:
                return float(group.group())
            raise ValueError(f"LLM response is not a number: {res}")
        return score

    def is_successful(self) -> bool:
        return self.success or False

    @property
    def __name__(self):
        return "RAG Relevancy"


@with_conversations
@pytest.mark.asyncio(scope="session")
@pytest.mark.repeat(10)  # Catch multi-threading and concurrency issues
async def test_relevancy(
    call: CallStateModel,
    claim_tests_excl: list[str],
    claim_tests_incl: list[str],
    deepeval_model: GPTModel,
    expected_output: str,
    inputs: list[str],
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
    call.lang = lang

    # Fill call with messages
    for input in inputs:
        call.messages.append(
            MessageModel(
                content=input,
                persona=MessagePersonaEnum.HUMAN,
            )
        )

    # Get trainings
    trainings = await call.trainings(cache_only=False)

    logger.info(f"Messages: {call.messages}")
    logger.info(f"Trainings: {trainings}")

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
    full_input = " ".join([message.content for message in call.messages])
    test_case = LLMTestCase(
        actual_output="",  # Not used
        input=full_input,
        retrieval_context=[
            TypeAdapter(TrainingModel)
            .dump_json(training, exclude=TrainingModel.excluded_fields_for_llm())
            .decode()
            for training in trainings
        ],
    )

    # Define LLM metrics
    llm_metrics = [
        RagRelevancyMetric(
            threshold=0.5, model=deepeval_model
        ),  # Compare input to the retrieval context
    ]

    # Execute LLM tests
    assert_test(test_case, llm_metrics)
