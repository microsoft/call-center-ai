from azure.core.exceptions import ServiceResponseError
from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi.encoders import jsonable_encoder
from helpers.config import CONFIG
from helpers.config_models.database import CosmosDbModel
from helpers.logging import build_logger
from models.call import CallModel
from persistence.istore import IStore
from pydantic import ValidationError
from typing import AsyncGenerator, List, Optional
from uuid import UUID
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)


_logger = build_logger(__name__)


class CosmosStore(IStore):
    _config: CosmosDbModel

    def __init__(self, config: CosmosDbModel):
        _logger.info(f"Using Cosmos DB {config.database}/{config.container}")
        self._config = config

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def call_aget(self, call_id: UUID) -> Optional[CallModel]:
        _logger.debug(f"Loading call {call_id}")
        res = None
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    query="SELECT * FROM c WHERE STRINGEQUALS(c.id, @id)",
                    parameters=[{"name": "@id", "value": str(call_id)}],
                )
                raw = await anext(items)
                try:
                    res = CallModel(**raw)
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        except StopAsyncIteration:
            pass
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e}")
        return res

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def call_aset(self, call: CallModel) -> bool:
        data = jsonable_encoder(call.model_dump(), exclude_none=True)
        data["id"] = str(call.call_id)  # CosmosDB requires an id field
        _logger.debug(f"Saving call {call.call_id}: {data}")
        try:
            async with self._use_db() as db:
                await db.upsert_item(body=data)
            return True
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB: {e}")
            return False

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def call_asearch_one(self, phone_number: str) -> Optional[CallModel]:
        _logger.debug(f"Loading last call for {phone_number}")
        res = None
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    max_item_count=1,
                    query=f"SELECT * FROM c WHERE (STRINGEQUALS(c.phone_number, @phone_number, true) OR STRINGEQUALS(c.claim.policyholder_phone, @phone_number, true)) AND c.created_at >= DATETIMEADD('hh', -{CONFIG.workflow.conversation_timeout_hour}, GETCURRENTDATETIME()) ORDER BY c.created_at DESC",
                    parameters=[
                        {
                            "name": "@phone_number",
                            "value": phone_number,
                        }
                    ],
                )
                raw = await anext(items)
                try:
                    res = CallModel(**raw)
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        except StopAsyncIteration:
            pass
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB: {e}")
        return res

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def call_asearch_all(self, phone_number: str) -> Optional[List[CallModel]]:
        _logger.debug(f"Loading all calls for {phone_number}")
        calls = []
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    query="SELECT * FROM c WHERE STRINGEQUALS(c.phone_number, @phone_number, true) OR STRINGEQUALS(c.claim.policyholder_phone, @phone_number, true) ORDER BY c.created_at DESC",
                    parameters=[
                        {
                            "name": "@phone_number",
                            "value": phone_number,
                        },
                    ],
                )
                async for raw in items:
                    if not raw:
                        continue
                    try:
                        calls.append(CallModel(**raw))
                    except ValidationError as e:
                        _logger.warn(f"Error parsing call: {e.errors()}")
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e}")
        return calls or None

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[ContainerProxy, None]:
        client = CosmosClient(
            # Reliability
            connection_timeout=10,
            consistency_level="BoundedStaleness",
            # Azure deployment
            url=self._config.endpoint,
            # Authentication with API key
            credential=self._config.access_key.get_secret_value(),
        )
        try:
            database = client.get_database_client(self._config.database)
            yield database.get_container_client(self._config.container)
        finally:
            await client.close()
