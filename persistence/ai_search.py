from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizableTextQuery
from contextlib import asynccontextmanager
from helpers.config import CONFIG
from helpers.config_models.ai_search import AiSearchModel
from helpers.logging import build_logger
from models.call import CallModel
from models.training import TrainingModel
from persistence.icache import ICache
from persistence.isearch import ISearch
from pydantic import TypeAdapter
from pydantic import ValidationError
from typing import AsyncGenerator, List, Optional


_logger = build_logger(__name__)


class AiSearchSearch(ISearch):
    _config: AiSearchModel

    def __init__(self, cache: ICache, config: AiSearchModel):
        _logger.info(f"Using AI Search {config.endpoint} with index {config.index}")
        _logger.info(
            f"Note: At ~300 chars /doc, each LLM call will use approx {300 * config.top_k * config.expansion_k / 4} tokens (without tools)"
        )
        self._config = config
        super().__init__(cache)

    async def training_asearch_all(
        self, text: str, call: CallModel
    ) -> Optional[List[TrainingModel]]:
        _logger.debug(f'Searching training data for "{text}"')
        if not text:
            return None

        # Try cache
        cache_key = f"{self.__class__.__name__}:training_asearch_all:{text}"
        cached = await self._cache.aget(cache_key)
        if cached:
            try:
                return TypeAdapter(List[TrainingModel]).validate_json(cached)
            except ValidationError:
                _logger.warn(f"Error parsing cached training: {cached}")
                pass

        # Try live
        trainings: List[TrainingModel] = []
        try:
            async with self._use_db() as db:
                results = await db.search(
                    # Full text search
                    query_type="semantic",
                    semantic_configuration_name=self._config.semantic_configuration,
                    search_fields=[
                        "content",
                        "title",
                    ],
                    search_text=text,
                    # Spell correction
                    query_language=call.lang.short_code,
                    query_speller="lexicon",
                    # Vector search
                    vector_queries=[
                        VectorizableTextQuery(
                            exhaustive=True,
                            fields="vectors",
                            k_nearest_neighbors=self._config.top_k,
                            text=text,
                        )
                    ],
                    # Return fields
                    select=[
                        "id",
                        "content",
                        "source_uri",
                        "title",
                    ],
                    top=self._config.top_k,
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
                        _logger.warn(f"Error parsing training: {e.errors()}")
        except HttpResponseError as e:
            _logger.error(f"Error requesting AI Search, {e}")
        except ServiceRequestError as e:
            _logger.error(f"Error connecting to AI Search, {e}")

        # Update cache
        await self._cache.aset(
            cache_key,
            (
                TypeAdapter(List[TrainingModel]).dump_json(trainings)
                if trainings
                else None
            ),
        )

        return trainings or None

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[SearchClient, None]:
        db = SearchClient(
            credential=AzureKeyCredential(self._config.access_key.get_secret_value()),
            endpoint=self._config.endpoint,
            index_name=self._config.index,
        )
        try:
            yield db
        finally:
            await db.close()
