from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    AzureOpenAIVectorizer,
    AzureOpenAIVectorizerParameters,
    HnswAlgorithmConfiguration,
    LexicalAnalyzerName,
    ScalarQuantizationCompression,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import (
    HybridCountAndFacetMode,
    HybridSearch,
    QueryLanguage,
    QueryType,
    ScoringStatistics,
    SearchMode,
    VectorizableTextQuery,
)
from pydantic import TypeAdapter, ValidationError
from tenacity import (
    retry,
    retry_any,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.helpers.cache import lru_acache
from app.helpers.config_models.ai_search import AiSearchModel
from app.helpers.http import azure_transport
from app.helpers.identity import credential
from app.helpers.logging import logger
from app.helpers.monitoring import suppress
from app.models.readiness import ReadinessEnum
from app.models.training import TrainingModel
from app.persistence.icache import ICache
from app.persistence.isearch import ISearch


class TooManyRequests(Exception):
    pass


class AiSearchSearch(ISearch):
    _client: SearchClient | None = None
    _config: AiSearchModel

    def __init__(self, cache: ICache, config: AiSearchModel):
        super().__init__(cache)
        self._config = config

    async def readiness(self) -> ReadinessEnum:
        """
        Check the readiness of the AI Search service.
        """
        try:
            async with await self._use_client() as client:
                await client.get_document_count()
            return ReadinessEnum.OK
        except HttpResponseError:
            logger.exception("Error requesting AI Search")
        except ServiceRequestError:
            logger.exception("Error connecting to AI Search")
        except Exception:
            logger.exception("Unknown error while checking AI Search readiness")
        return ReadinessEnum.FAIL

    @retry(
        reraise=True,
        retry=retry_any(
            retry_if_exception_type(ServiceResponseError),
            retry_if_exception_type(TooManyRequests),
        ),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.8, max=8),
    )
    async def training_search_all(
        self,
        lang: str,
        text: str,
        cache_only: bool = False,
    ) -> list[TrainingModel] | None:
        # logger.debug('Searching training data for "%s"', text)
        if not text:
            return None

        # Try cache
        cache_key = f"{self.__class__.__name__}-training_asearch_all-v2-{text}"  # Cache sort method has been updated in v6, thus the v2
        cached = await self._cache.get(cache_key)
        if cached:
            try:
                return TypeAdapter(list[TrainingModel]).validate_json(cached)
            except ValidationError as e:
                logger.debug("Parsing error: %s", e.errors())

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
                        logger.debug("Parsing error: %s", e.errors())
        except ResourceNotFoundError:
            logger.warning('AI Search index "%s" not found', self._config.index)
        except HttpResponseError as e:
            message = e.message.lower()
            if "too many requests" in message or "exceed the limits" in message:
                raise TooManyRequests()
            logger.error("Error requesting AI Search: %s", e)
        except ServiceRequestError as e:
            logger.error("Error connecting to AI Search: %s", e)

        # Update cache
        if trainings:
            await self._cache.set(
                key=cache_key,
                ttl_sec=60 * 60 * 24,  # 1 day
                value=TypeAdapter(list[TrainingModel]).dump_json(trainings),
            )

        return trainings or None

    @lru_acache()
    async def _use_client(self) -> SearchClient:
        """
        Get the search client.

        If the index does not exist, it will be created.
        """
        logger.debug("Using AI Search client for %s", self._config.index)
        logger.debug(
            "Note: At ~300 chars /doc, each LLM call will use approx %d tokens (without tools)",
            300 * self._config.top_n_documents * self._config.expansion_n_messages / 4,
        )

        # Index configuration
        fields = [
            # Required field for indexing key
            SimpleField(
                name="id",
                key=True,
                type=SearchFieldDataType.String,
            ),
            # Custom fields
            SearchableField(
                analyzer_name=LexicalAnalyzerName.STANDARD_LUCENE,
                name="content",
                type=SearchFieldDataType.String,
            ),
            SearchableField(
                analyzer_name=LexicalAnalyzerName.STANDARD_LUCENE,
                name="title",
                type=SearchFieldDataType.String,
            ),
            SearchField(
                name="vectors",
                searchable=True,
                stored=False,
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                vector_search_dimensions=self._config.embedding_dimensions,
                vector_search_profile_name="profile-default",
            ),
        ]
        vector_search = VectorSearch(
            profiles=[
                VectorSearchProfile(
                    algorithm_configuration_name="algorithm-default",
                    compression_name="compression-scalar",
                    name="profile-default",
                    vectorizer_name="vectorizer-default",
                ),
            ],
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="algorithm-default",
                ),
            ],
            vectorizers=[
                AzureOpenAIVectorizer(
                    vectorizer_name="vectorizer-default",
                    # Without credentials specified, the database will use its system managed identity
                    parameters=AzureOpenAIVectorizerParameters(
                        deployment_name=self._config.embedding_deployment,
                        model_name=self._config.embedding_model,
                        resource_url=self._config.embedding_endpoint,
                    ),
                )
            ],
            # Eliminate redundant vectors
            # See: https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-storage-options
            compressions=[
                ScalarQuantizationCompression(
                    compression_name="compression-scalar",
                ),
            ],
        )
        semantic_search = SemanticSearch(
            default_configuration_name=self._config.semantic_configuration,
            configurations=[
                SemanticConfiguration(
                    name=self._config.semantic_configuration,
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(
                            field_name="title",
                        ),
                        content_fields=[
                            SemanticField(
                                field_name="content",
                            ),
                        ],
                    ),
                ),
            ],
        )

        # Create index if it does not exist
        async with SearchIndexClient(
            # Deployment
            endpoint=self._config.endpoint,
            index_name=self._config.index,
            # Performance
            transport=await azure_transport(),
            # Authentication
            credential=await credential(),
        ) as client:
            try:
                with suppress(ResourceExistsError):
                    await client.create_index(
                        SearchIndex(
                            fields=fields,
                            name=self._config.index,
                            semantic_search=semantic_search,
                            vector_search=vector_search,
                        )
                    )
                    logger.info('Created Search "%s"', self._config.index)
            except HttpResponseError as e:
                if not e.error or not e.error.code == "ResourceNameAlreadyInUse":
                    raise e

        # Return client
        return SearchClient(
            # Deployment
            endpoint=self._config.endpoint,
            index_name=self._config.index,
            # Performance
            transport=await azure_transport(),
            # Authentication
            credential=await credential(),
        )
