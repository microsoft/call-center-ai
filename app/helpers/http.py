from aiohttp import (
    AsyncResolver,
    ClientSession,
    ClientTimeout,
    DummyCookieJar,
    TCPConnector,
)
from aiohttp_retry import JitterRetry, RetryClient
from azure.core.pipeline.transport._aiohttp import AioHttpTransport
from twilio.http.async_http_client import AsyncTwilioHttpClient

from app.helpers.cache import lru_acache


@lru_acache()
async def _aiohttp_cookie_jar() -> DummyCookieJar:
    """
    Create a cookie jar mock for AIOHTTP.

    Object is cached for performance.

    Returns a `DummyCookieJar` instance.
    """
    return DummyCookieJar()


@lru_acache()
async def aiohttp_session() -> ClientSession:
    """
    Create an AIOHTTP session.

    Object is cached for performance.

    Returns a `ClientSession` instance.
    """
    return ClientSession(
        # Same config as default in the SDK
        auto_decompress=False,
        cookie_jar=await _aiohttp_cookie_jar(),
        trust_env=True,
        # Performance
        connector=TCPConnector(resolver=AsyncResolver()),
        # Reliability
        timeout=ClientTimeout(
            connect=5,
            total=60,
        ),
    )


@lru_acache()
async def azure_transport() -> AioHttpTransport:
    """
    Create an AIOHTTP transport, for Azure SDK.

    Object is cached for performance.

    Returns a `AioHttpTransport` instance.
    """
    # Azure SDK implements its own retry logic (e.g. for Cosmos DB), so we don't add it here
    return AioHttpTransport(
        session_owner=False,  # Restrict the SDK to close the client after usage
        session=await aiohttp_session(),
    )


@lru_acache()
async def twilio_http() -> AsyncTwilioHttpClient:
    """
    Create a Twilio HTTP client.

    Object is cached for performance.

    Returns a `AsyncTwilioHttpClient` instance.
    """
    _twilio_http = AsyncTwilioHttpClient(
        timeout=10,
    )
    _twilio_http.session = RetryClient(
        client_session=await aiohttp_session(),
        # Reliability
        retry_options=JitterRetry(
            attempts=3,
            max_timeout=8,
            start_timeout=0.8,
        ),  # Twilio SDK outsources its retry logic to AIOHTTP
    )
    return _twilio_http
