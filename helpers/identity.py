from collections.abc import Awaitable, Callable

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

from helpers.http import azure_transport

_client: DefaultAzureCredential | None = None


async def credential() -> DefaultAzureCredential:
    global _client  # noqa: PLW0603
    if not _client:
        _client = DefaultAzureCredential(
            # Performance
            transport=await azure_transport(),
        )
    return _client


async def token(service: str) -> Callable[[], Awaitable[str]]:
    return get_bearer_token_provider(await credential(), service)
