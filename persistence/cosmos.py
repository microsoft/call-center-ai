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


_logger = build_logger(__name__)


class CosmosStore(IStore):
    _config: CosmosDbModel

    def __init__(self, config: CosmosDbModel):
        _logger.info(f"Using Cosmos DB {config.database}/{config.container}")
        self._config = config

    async def call_aget(self, call_id: UUID) -> Optional[CallModel]:
        _logger.debug(f"Loading call {call_id}")
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    query="SELECT * FROM c WHERE STRINGEQUALS(c.id, @id)",
                    parameters=[{"name": "@id", "value": str(call_id)}],
                )
                raw = await anext(items)
                try:
                    return CallModel(**raw)
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        except StopAsyncIteration:
            return None
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e}")

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

    async def call_asearch_one(self, phone_number: str) -> Optional[CallModel]:
        _logger.debug(f"Loading last call for {phone_number}")
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    max_item_count=1,
                    query="SELECT * FROM c WHERE (STRINGEQUALS(c.phone_number, @phone_number, true) OR STRINGEQUALS(c.claim.policyholder_phone, @phone_number, true)) AND c.created_at < @date_limit ORDER BY c.created_at DESC",
                    parameters=[
                        {
                            "name": "@phone_number",
                            "value": phone_number,
                        },
                        {
                            "name": "@date_limit",
                            "value": str(
                                datetime.utcnow()
                                + timedelta(
                                    hours=CONFIG.workflow.conversation_timeout_hour
                                )
                            ),
                        },
                    ],
                )
                raw = await anext(items)
                try:
                    return CallModel(**raw)
                except ValidationError as e:
                    _logger.warn(f"Error parsing call: {e.errors()}")
        except StopAsyncIteration:
            return None
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB: {e}")

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
            connection_timeout=5,
            # Azure deployment
            url=self._config.endpoint,
            # Authentication with API key
            credential=self._config.access_key.get_secret_value(),
        )
        database = client.get_database_client(self._config.database)
        yield database.get_container_client(self._config.container)
        await client.close()
