from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizableTextQuery
from contextlib import asynccontextmanager
from helpers.config import CONFIG
from helpers.config_models.ai_search import AiSearchModel
from helpers.logging import build_logger
from models.training import TrainingModel
from persistence.isearch import ISearch
from pydantic import ValidationError
from typing import AsyncGenerator, List, Optional


_logger = build_logger(__name__)


class AiSearchSearch(ISearch):
    _config: AiSearchModel

    def __init__(self, config: AiSearchModel):
        _logger.info(f"Using AI Search {config.endpoint} with index {config.index}")
        self._config = config

    async def training_asearch_all(self, text: str) -> Optional[List[TrainingModel]]:
        _logger.debug(f'Searching training data for "{text}"')
        if not text:
            return None
        trainings = []
        try:
            async with self._use_db() as db:
                results = await db.search(
                    query_language=CONFIG.workflow.conversation_lang,  # Re-rank results based on language
                    query_speller="lexicon",  # Spell correction
                    query_type="semantic",  # Enable semantic search
                    search_text=text,
                    select=["id", "content", "source_uri", "title"],
                    semantic_configuration_name=self._config.semantic_configuration,
                    top=self._config.top_k,
                    vector_queries=[
                        VectorizableTextQuery(
                            fields="vectors",
                            k=self._config.top_k,
                            text=text,
                        )
                    ],  # Enable vector search
                )
                async for result in results:
                    if not result:
                        continue
                    try:
                        trainings.append(
                            TrainingModel.model_validate(
                                {
                                    **result,
                                    "score": result["@search.score"],
                                }
                            )
                        )
                    except ValidationError as e:
                        _logger.warn(f"Error parsing training, {e.message}")
        except HttpResponseError as e:
            _logger.error(f"Error connecting to AI Search, {e.message}")
        return trainings or None

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[SearchClient, None]:
        async with SearchClient(
            credential=AzureKeyCredential(self._config.access_key.get_secret_value()),
            endpoint=self._config.endpoint,
            index_name=self._config.index,
        ) as db:
            yield db
