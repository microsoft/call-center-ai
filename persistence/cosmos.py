from azure.cosmos import CosmosClient, ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from azure.identity import DefaultAzureCredential
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi.encoders import jsonable_encoder
from helpers.config import CONFIG
from helpers.config_models.database import CosmosDbModel
from helpers.logging import build_logger
from models.call import CallModel
from persistence.istore import IStore
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_random_exponential
from typing import AsyncGenerator, List, Optional
from uuid import UUID


_logger = build_logger(__name__)
AZ_CREDENTIAL = DefaultAzureCredential()


class CosmosStore(IStore):
    _db: ContainerProxy

    def __init__(self, config: CosmosDbModel):
        _logger.info(f"Using CosmosDB {config.database}/{config.container}")
        client = CosmosClient(
            connection_timeout=5, credential=AZ_CREDENTIAL, url=config.endpoint
        )
        database = client.get_database_client(config.database)
        self._db = database.get_container_client(config.container)

    async def call_aget(self, call_id: UUID) -> Optional[CallModel]:
        _logger.debug(f"Loading call {call_id}")
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    enable_cross_partition_query=True,
                    query="SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": str(call_id)}],
                )
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e.message}")
            return None
        try:
            raw = next(items)
            try:
                return CallModel(**raw)
            except ValidationError as e:
                _logger.warn(f"Error parsing call, {e.message}")
        except StopIteration:
            return None

    async def call_aset(self, call: CallModel) -> bool:
        data = jsonable_encoder(call, exclude_none=True)
        data["id"] = str(call.call_id)  # CosmosDB requires an id field
        _logger.debug(f"Saving call {call.call_id}: {data}")
        try:
            self._db.upsert_item(body=data)
            return True
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB: {e.message}")
            return False

    async def call_asearch_one(self, phone_number: str) -> Optional[CallModel]:
        _logger.debug(f"Loading last call for {phone_number}")
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    max_item_count=1,
                    partition_key=phone_number,
                    query="SELECT * FROM c WHERE c.created_at < @date_limit ORDER BY c.created_at DESC",
                    parameters=[
                        {
                            "name": "@date_limit",
                            "value": str(
                                datetime.utcnow()
                                + timedelta(hours=CONFIG.workflow.conversation_timeout_hour)
                            ),
                        }
                    ],
                )
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB: {e.message}")
            return None
        try:
            raw = next(items)
            try:
                return CallModel(**raw)
            except ValidationError as e:
                _logger.warn(f"Error parsing call, {e.message}")
        except StopIteration:
            return None

    async def call_asearch_all(self, phone_number: str) -> Optional[List[CallModel]]:
        _logger.debug(f"Loading all calls for {phone_number}")
        calls = []
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    partition_key=phone_number,
                    query="SELECT * FROM c ORDER BY c.created_at DESC",
                )
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e.message}")
            return None
        for raw in items:
            if not raw:
                continue
            try:
                calls.append(CallModel(**raw))
            except ValidationError as e:
                _logger.warn(f"Error parsing call, {e.message}")
        return calls or None

    @retry(
        stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=0.5, max=30)
    )
    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[ContainerProxy, None]:
        yield self._db
