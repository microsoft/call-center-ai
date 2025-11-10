"""Local in-memory queue for development (no Azure dependencies)."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable

from app.helpers.cache import get_scheduler
from app.helpers.logging import logger
from app.persistence.azure_queue_storage import Message


class LocalQueue:
    """
    Simple in-memory queue for local development.

    This queue provides the same interface as AzureQueueStorage but stores
    messages in memory. Great for development and testing without Azure dependencies.

    Note: Messages are lost when the application restarts.
    For persistent local queuing, consider using Redis or a file-based queue.
    """

    _name: str
    _queue: asyncio.Queue[Message]
    _message_counter: int = 0

    def __init__(self, name: str):
        self._name = name
        self._queue = asyncio.Queue()
        logger.info("Using local in-memory queue: %s", name)

    async def send_message(self, message: str) -> None:
        """Send a message to the queue."""
        self._message_counter += 1
        msg = Message(
            content=message,
            delete_token=f"delete-{self._message_counter}",
            dequeue_count=0,
            message_id=str(self._message_counter),
        )
        await self._queue.put(msg)
        logger.debug("Message sent to local queue %s", self._name)

    async def receive_messages(
        self,
        max_messages: int,
        visibility_timeout: int,  # Not used in local queue
    ) -> AsyncGenerator[Message]:
        """
        Receive messages from the queue.

        Note: visibility_timeout is ignored for the local queue implementation.
        """
        received = 0
        while received < max_messages and not self._queue.empty():
            try:
                # Non-blocking get with timeout
                message = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                yield message
                received += 1
            except asyncio.TimeoutError:
                break

    async def delete_message(self, message: Message) -> None:
        """
        Mark message as processed (no-op for local queue).

        In the local queue, messages are removed when dequeued,
        so this is just a no-op for interface compatibility.
        """
        logger.debug("Message %s marked as processed", message.message_id)

    async def trigger(
        self,
        arg: str,
        func: Callable[..., Awaitable],
    ) -> None:
        """
        Trigger a local function when a message is received.

        Continuously polls the queue and processes messages.
        """
        logger.info(
            'Local Queue "%s" is set to trigger function "%s"',
            self._name,
            func.__name__,
        )
        async with get_scheduler() as scheduler:
            try:
                # Loop forever to receive messages
                while True:
                    # Check for messages
                    messages_received = False
                    async for message in self.receive_messages(
                        max_messages=32,
                        visibility_timeout=32 * 5,  # Not used but kept for compatibility
                    ):
                        messages_received = True
                        await scheduler.spawn(
                            self._process_message(
                                arg=arg,
                                func=func,
                                message=message,
                            )
                        )

                    # Sleep a bit to avoid busy-waiting
                    # If we processed messages, check again quickly
                    # Otherwise, wait longer
                    await asyncio.sleep(0.1 if messages_received else 1)

            except asyncio.CancelledError:
                logger.debug('Local Queue "%s" trigger task cancelled', self._name)
            finally:
                await scheduler.close()

    async def _process_message(
        self,
        arg: str,
        func: Callable[..., Awaitable],
        message: Message,
    ) -> None:
        """Process a message with a function."""
        kwargs = {}
        kwargs[arg] = message
        await func(**kwargs)
        # Message is already removed from queue, so no need to delete
