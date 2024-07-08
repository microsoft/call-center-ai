from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import (
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import (
    HybridCountAndFacetMode,
    HybridSearch,
    QueryLanguage,
    QueryType,
    ScoringStatistics,
    SearchMode,
    VectorizableTextQuery,
)
from helpers.http import azure_transport
from helpers.config_models.ai_search import AiSearchModel
from helpers.logging import logger
from models.readiness import ReadinessEnum
from models.training import TrainingModel
from persistence.icache import ICache
from persistence.isearch import ISearch
from pydantic import TypeAdapter
from pydantic import ValidationError
from typing import Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)


class AiSearchSearch(ISearch):
    _client: Optional[SearchClient] = None
    _config: AiSearchModel

    def __init__(self, cache: ICache, config: AiSearchModel):
        super().__init__(cache)
        logger.info(f"Using AI Search {config.endpoint} with index {config.index}")
        logger.info(
            f"Note: At ~300 chars /doc, each LLM call will use approx {300 * config.top_n_documents * config.expansion_n_messages / 4} tokens (without tools)"
        )
        self._config = config

    async def areadiness(self) -> ReadinessEnum:
        """
        Check the readiness of the AI Search service.
        """
        try:
            async with await self._use_client() as client:
                await client.get_document_count()
            return ReadinessEnum.OK
        except HttpResponseError:
            logger.error("Error requesting AI Search", exc_info=True)
        except ServiceRequestError:
            logger.error("Error connecting to AI Search", exc_info=True)
        except Exception:
            logger.error(
                "Unknown error while checking AI Search readiness", exc_info=True
            )
        return ReadinessEnum.FAIL

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.8, max=8),
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
            except ValidationError as e:
                logger.debug(f"Parsing error: {e.errors()}")

        if cache_only:
            return None

        # Try live
        trainings: list[TrainingModel] = []
        try:
            async with await self._use_client() as client:
                results = await client.search(
                    # Full text search
                    query_language=QueryLanguage(lang.lower()),
                    query_type=QueryType.SEMANTIC,
                    search_mode=SearchMode.ANY,  # Any of the terms will match
                    search_text=text,
                    semantic_configuration_name=self._config.semantic_configuration,
                    # Vector search
                    vector_queries=[
                        VectorizableTextQuery(
                            fields="vectors",
                            text=text,
                        )
                    ],
                    # Hybrid search (full text + vector search)
                    hybrid_search=HybridSearch(
                        count_and_facet_mode=HybridCountAndFacetMode.COUNT_RETRIEVABLE_RESULTS,
                        max_text_recall_size=1000,
                    ),
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
        except ResourceNotFoundError as e:
            logger.warning(f'AI Search index "{self._config.index}" not found')
        except HttpResponseError as e:
            logger.error(f"Error requesting AI Search: {e}")
        except ServiceRequestError as e:
            logger.error(f"Error connecting to AI Search: {e}")

        # Update cache
        if trainings:
            await self._cache.aset(
                cache_key, TypeAdapter(list[TrainingModel]).dump_json(trainings)
            )

        return trainings or None

    async def _use_client(self) -> SearchClient:
        if not self._client:
            self._client = SearchClient(
                # Deployment
                endpoint=self._config.endpoint,
                index_name=self._config.index,
                # Performance
                transport=await azure_transport(),
                # Authentication
                credential=AzureKeyCredential(
                    self._config.access_key.get_secret_value()
                ),
            )
        return self._client
