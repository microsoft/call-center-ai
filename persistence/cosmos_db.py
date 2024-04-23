from azure.core.exceptions import ServiceResponseError
from azure.cosmos.aio import CosmosClient, ContainerProxy
from azure.cosmos.exceptions import CosmosHttpResponseError
from contextlib import asynccontextmanager
from fastapi.encoders import jsonable_encoder
from helpers.config import CONFIG
from helpers.config_models.database import CosmosDbModel
from helpers.logging import build_logger
from models.call import CallModel
from models.readiness import ReadinessStatus
from persistence.istore import IStore
from pydantic import ValidationError
from typing import AsyncGenerator, Optional
from uuid import UUID, uuid4
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
)


_logger = build_logger(__name__)


class CosmosDbStore(IStore):
    _config: CosmosDbModel

    def __init__(self, config: CosmosDbModel):
        _logger.info(f"Using Cosmos DB {config.database}/{config.container}")
        self._config = config

    async def areadiness(self) -> ReadinessStatus:
        """
        Check the readiness of the Cosmos DB service.

        This will validate the ACID properties of the database: Create, Read, Update, Delete.
        """
        test_id = str(uuid4())
        test_partition = "+33612345678"
        test_dict = {
            "id": test_id,  # unique id
            "phone_number": test_partition,  # partition key
            "test": "test",
        }
        try:
            async with self._use_db() as db:
                # Test the item does not exist
                try:
                    await db.read_item(item=test_id, partition_key=test_partition)
                    return ReadinessStatus.FAIL
                except CosmosHttpResponseError as e:
                    if e.status_code != 404:
                        _logger.error(f"Error requesting CosmosDB, {e}")
                        return ReadinessStatus.FAIL
                # Create a new item
                await db.upsert_item(body=test_dict)
                # Test the item is the same
                read_item = await db.read_item(
                    item=test_id, partition_key=test_partition
                )
                assert {
                    k: v for k, v in read_item.items() if k in test_dict
                } == test_dict  # Check only the relevant fields, Cosmos DB adds metadata
                # Delete the item
                await db.delete_item(item=test_id, partition_key=test_partition)
                # Test the item does not exist
                try:
                    await db.read_item(item=test_id, partition_key=test_partition)
                    return ReadinessStatus.FAIL
                except CosmosHttpResponseError as e:
                    if e.status_code != 404:
                        _logger.error(f"Error requesting CosmosDB, {e}")
                        return ReadinessStatus.FAIL
            return ReadinessStatus.OK
        except AssertionError:
            _logger.error("Readiness test failed", exc_info=True)
        except CosmosHttpResponseError as e:
            _logger.error(f"Error requesting CosmosDB, {e}")
        return ReadinessStatus.FAIL

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def call_aget(self, call_id: UUID) -> Optional[CallModel]:
        _logger.debug(f"Loading call {call_id}")
        call = None
        try:
            async with self._use_db() as db:
                items = db.query_items(
                    query="SELECT * FROM c WHERE STRINGEQUALS(c.id, @id)",
                    parameters=[{"name": "@id", "value": str(call_id)}],
                )
                raw = await anext(items)
                try:
                    call = CallModel(**raw)
                except ValidationError as e:
                    _logger.warning(f"Error parsing call: {e.errors()}")
        except StopAsyncIteration:
            pass
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e}")
        return call

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
        call = None
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
                    call = CallModel(**raw)
                except ValidationError as e:
                    _logger.warning(f"Error parsing call: {e.errors()}")
        except StopAsyncIteration:
            pass
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB: {e}")
        return call

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceResponseError),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=0.5, max=30),
    )
    async def call_asearch_all(self, phone_number: str) -> Optional[list[CallModel]]:
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
                        _logger.warning(f"Error parsing call: {e.errors()}")
        except CosmosHttpResponseError as e:
            _logger.error(f"Error accessing CosmosDB, {e}")
        return calls or None

    @asynccontextmanager
    async def _use_db(self) -> AsyncGenerator[ContainerProxy, None]:
        """
        Generate the Cosmos DB client and close it after use.
        """
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
