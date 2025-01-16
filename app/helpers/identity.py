from collections.abc import Awaitable, Callable

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

from app.helpers.cache import lru_acache
from app.helpers.http import azure_transport


@lru_acache()
async def credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(
        # Performance
        transport=await azure_transport(),
    )


@lru_acache()
async def token(service: str) -> Callable[[], Awaitable[str]]:
    return get_bearer_token_provider(await credential(), service)
