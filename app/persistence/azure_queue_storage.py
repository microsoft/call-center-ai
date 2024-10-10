import asyncio
from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from azure.core.exceptions import ServiceRequestError
from azure.storage.queue.aio import QueueClient, QueueServiceClient
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from app.helpers.http import azure_transport
from app.helpers.identity import credential
from app.helpers.logging import logger


class Message(BaseModel):
    content: str
    delete_token: str | None
    dequeue_count: int | None
    message_id: str


class AzureQueueStorage:
    _account_url: str
    _encoding = "utf-8"
    _name: str

    def __init__(
        self,
        account_url: str,
        name: str,
    ) -> None:
        logger.info('Azure Queue Storage "%s" is configured', name)
        self._account_url = account_url
        self._name = name

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceRequestError),  # Catch for network errors
        stop=stop_after_attempt(8),
        wait=wait_random_exponential(multiplier=0.8, max=60),
    )
    async def send_message(
        self,
        message: str,
    ) -> None:
        async with self._use_client() as client:
            await client.send_message(self._escape(message))

    @retry(
        reraise=True,
        retry=retry_if_exception_type(ServiceRequestError),  # Catch for network errors
        stop=stop_after_attempt(8),
        wait=wait_random_exponential(multiplier=0.8, max=60),
    )
    async def receive_messages(
        self,
        max_messages: int,
        visibility_timeout: int,
    ) -> AsyncGenerator[Message, None]:
        async with self._use_client() as client:
            messages = client.receive_messages(
                max_messages=max_messages,
                visibility_timeout=visibility_timeout,
            )
            async for message in messages:
                yield Message(
                    content=self._unescape(message.content),
                    delete_token=message.pop_receipt,
                    dequeue_count=message.dequeue_count,
                    message_id=message.id,
                )

    async def delete_message(
        self,
        message: Message,
    ) -> None:
        async with self._use_client() as client:
            await client.delete_message(
                message=message.message_id,
                pop_receipt=message.delete_token,
            )

    def _escape(self, value: str) -> str:
        """
        Escape value to base64 encoding.
        """
        return b64encode(value.encode(self._encoding)).decode(self._encoding)

    def _unescape(self, value: str) -> str:
        """
        Unescape value from base64 encoding.

        If the value is not base64 encoded, return the original value as string. This will handle retro-compatibility with old messages.
        """
        try:
            return b64decode(value.encode(self._encoding)).decode(self._encoding)
        except (UnicodeDecodeError, BinasciiError):
            return value

    @asynccontextmanager
    async def _use_client(self) -> AsyncGenerator[QueueClient, None]:
        async with QueueServiceClient(
            # Performance
            transport=await azure_transport(),
            # Deployment
            account_url=self._account_url,
            # Authentication
            credential=await credential(),
        ) as service:
            yield service.get_queue_client(
                # Performance
                transport=await azure_transport(),
                # Deployment
                queue=self._name,
            )

    async def trigger(
        self,
        arg: str,
        func: Callable[..., Awaitable],
    ):
        """
        Trigger a local function when a message is received.
        """
        logger.info(
            'Azure Queue Storage "%s" is set to trigger function "%s"',
            self._name,
            func.__name__,
        )
        # Loop forever to receive messages
        while messages := self.receive_messages(
            max_messages=32,
            visibility_timeout=32 * 5,  # 5 secs per message
        ):
            # Process messages
            async for message in messages:
                # Call function with the selected argument name
                kwargs = {}
                kwargs[arg] = message
                # First, call function
                await func(**kwargs)
                # Then, delete message
                await self.delete_message(message)
            # Add a small delay to avoid tight loop
            await asyncio.sleep(1)
