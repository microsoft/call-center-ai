from deepeval.models.base_model import DeepEvalBaseLLM
from helpers.config import CONFIG
from helpers.logging import build_logger
from models.call import CallModel
from models.training import TrainingModel
import pytest


_logger = build_logger(__name__)
_search = CONFIG.ai_search.instance()


@pytest.mark.parametrize(
    "user_message, user_lang",
    [
        pytest.param(
            "accident de voiture sur autoroute",
            "fr-FR",
            id="car_fr",
        ),
        pytest.param(
            "cyber-attaque par DDOS sur mon entreprise",
            "fr-FR",
            id="cyberattack_fr",
        ),
        pytest.param(
            "refus passage expert souhaite contre-expertise",
            "fr-FR",
            id="expertise_fr",
        ),
        pytest.param(
            "grêlons champ réculte tomates perdue indemnisation",
            "fr-FR",
            id="hail_fr",
        ),
    ],
)
@pytest.mark.asyncio
async def test_relevancy(
    call_mock: CallModel,
    deepeval_model: DeepEvalBaseLLM,
    user_lang: str,
    user_message: str,
) -> None:
    # Configure context
    call_mock.lang.short_code = user_lang

    # Init data
    data_models = await _search.training_asearch_all(user_message, call_mock)
    data_str = ", ".join([d.content for d in data_models or []])

    _logger.info(f"User message: {user_message}")
    _logger.info(f"Data: {data_str}")

    # Ask the LLM
    llm_res = await deepeval_model.a_generate(
        f"""
        Assistant is a data analyst expert with 20 years of experience.

        # Objective
        The assistant will analyze the input data and decide whether it would be useful to respond to the user's message.

        # Context
        The data is a JSON list and comes from a database. The data has been stemmed.

        # Rules
        - Answer only with the float value, never add other text
        - Response 0.0 means not useful at all, 1.0 means totally useful

        # Input data
        {data_str}

        # User message
        {user_message}

        # Response format
        A float from 0.0 to 1.0

        ## Example 1
        Input data: bananas are yellow, apples are red
        User message: I love bananas
        Assistant: 1

        ## Example 2
        Input data: mouse is a rodent, mouse is a computer peripheral
        User message: The sky is blue
        Assistant: 0

        ## Example 3
        Input data: car accidents must be reported within 24 hours, car accidents are dangerous
        User message: my car is stuck in the mud
        Assistant: 0.7
    """
    )
    try:
        llm_score = float(llm_res)
    except ValueError:
        raise ValueError(f"LLM response is not a number: {llm_res}")

    # Assert
    assert (
        llm_score >= 0.5
    ), f"Analysis failed for {user_message}, LLM response {llm_score}"
