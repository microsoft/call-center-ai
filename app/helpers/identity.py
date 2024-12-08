from collections.abc import Awaitable, Callable

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

from app.helpers.cache import async_lru_cache
from app.helpers.http import azure_transport


@async_lru_cache()
async def credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(
        # Performance
        transport=await azure_transport(),
    )


@async_lru_cache()
async def token(service: str) -> Callable[[], Awaitable[str]]:
    return get_bearer_token_provider(await credential(), service)
