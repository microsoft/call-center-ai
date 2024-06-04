from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import (
    HttpResponseError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import (
    QueryLanguage,
    QueryType,
    ScoringStatistics,
    SearchMode,
    VectorizableTextQuery,
)
from contextlib import asynccontextmanager
from helpers.config_models.ai_search import AiSearchModel
from helpers.logging import logger
from models.readiness import ReadinessStatus
from models.training import TrainingModel
from persistence.icache import ICache
from persistence.isearch import ISearch
from pydantic import TypeAdapter
from pydantic import ValidationError
from typing import AsyncGenerator, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)


class AiSearchSearch(ISearch):
    _config: AiSearchModel

    def __init__(self, cache: ICache, config: AiSearchModel):
        logger.info(f"Using AI Search {config.endpoint} with index {config.index}")
        logger.info(
            f"Note: At ~300 chars /doc, each LLM call will use approx {300 * config.top_n_documents * config.expansion_n_messages / 4} tokens (without tools)"
        )
        self._config = config
        super().__init__(cache)

    async def areadiness(self) -> ReadinessStatus:
        """
        Check the readiness of the AI Search service.
        """
        try:
            async with self._use_db() as db:
                await db.get_document_count()
            return ReadinessStatus.OK
        except HttpResponseError as e:
            logger.error(f"Error requesting AI Search, {e}")
        except ServiceRequestError as e:
            logger.error(f"Error connecting to AI Search, {e}")
        return ReadinessStatus.FAIL

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def training_asearch_all(
        self,
        lang: str,
        text: str,
        cache_only: bool = False,
    ) -> Optional[list[TrainingModel]]:
        logger.debug(f'Searching training data for "{text}"')
        if not text:
            return None

        # Try cache
        cache_key = f"{self.__class__.__name__}-training_asearch_all-v2-{text}"  # Cache sort method has been updated in v6, thus the v2
        cached = await self._cache.aget(cache_key)
        if cached:
            try:
                return TypeAdapter(list[TrainingModel]).validate_json(cached)
            except ValidationError:
                logger.warning(f"Error parsing cached training: {cached}")
                pass

        if cache_only:
            return None

        # Try live
        trainings: list[TrainingModel] = []
        try:
            async with self._use_db() as db:
                results = await db.search(
                    # Full text search
                    query_language=QueryLanguage(lang.lower()),
                    query_type=QueryType.SEMANTIC,
                    semantic_configuration_name=self._config.semantic_configuration,
                    search_fields=[
                        "content",
                        "title",
                    ],
                    search_mode=SearchMode.ANY,  # Any of the terms will match
                    search_text=text,
                    # Vector search
                    vector_queries=[
                        VectorizableTextQuery(
                            fields="vectors",
                            text=text,
                        )
                    ],
                    # Relability
                    semantic_max_wait_in_milliseconds=750,  # Timeout in ms
                    # Return fields
                    include_total_count=False,  # Total count is not used
                    query_caption_highlight_enabled=False,  # Highlighting is not used
                    scoring_statistics=ScoringStatistics.GLOBAL,  # Evaluate scores in the backend for more accurate values
                    top=self._config.top_n_documents,
                )
                async for result in results:
                    try:
                        trainings.append(
                            TrainingModel.model_validate(
                                {
                                    **result,
                                    "score": (
                                        (result["@search.reranker_score"] / 4 * 5)
                                        if "@search.reranker_score" in result
                                        else (result["@search.score"] * 5)
                                    ),  # Normalize score to 0-5, failback to search score if reranker is not available
                                }
                            )
                        )
                    except ValidationError as e:
                        logger.debug(f"Parsing error: {e.errors()}")
        except HttpResponseError as e:
            logger.error(f"Error requesting AI Search, {e}")
        except ServiceRequestError as e:
            logger.error(f"Error connecting to AI Search, {e}")

        # Update cache
        await self._cache.aset(
            cache_key,
            (
                TypeAdapter(list[TrainingModel]).dump_json(trainings)
                if trainings
                else None
            ),
        )

        return trainings or None

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[SearchClient, None]:
        """
        Generate the AI Search client and close it after use.
        """
        db = SearchClient(
            credential=AzureKeyCredential(self._config.access_key.get_secret_value()),
            endpoint=self._config.endpoint,
            index_name=self._config.index,
        )
        try:
            yield db
        finally:
            await db.close()
