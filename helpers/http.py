from typing import Optional

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
from aiohttp_retry import JitterRetry, RetryClient
from azure.core.pipeline.transport._aiohttp import AioHttpTransport
from twilio.http.async_http_client import AsyncTwilioHttpClient

_cookie_jar: Optional[DummyCookieJar] = None
_session: Optional[ClientSession] = None
_transport: Optional[AioHttpTransport] = None
_twilio_http: Optional[AsyncTwilioHttpClient] = None


async def _aiohttp_cookie_jar() -> DummyCookieJar:
    """
    Create a cookie jar mock for AIOHTTP.

    Object is cached for performance.

    Returns a `DummyCookieJar` instance.
    """
    global _cookie_jar  # pylint: disable=global-statement
    if not _cookie_jar:
        _cookie_jar = DummyCookieJar()
    return _cookie_jar


async def aiohttp_session() -> ClientSession:
    """
    Create an AIOHTTP session.

    Object is cached for performance.

    Returns a `ClientSession` instance.
    """
    global _session  # pylint: disable=global-statement
    if not _session:
        _session = ClientSession(
            # Same config as default in the SDK
            auto_decompress=False,
            cookie_jar=await _aiohttp_cookie_jar(),
            trust_env=True,
            # Reliability
            timeout=ClientTimeout(
                connect=5,
                total=60,
            ),
        )
    return _session


async def azure_transport() -> AioHttpTransport:
    """
    Create an AIOHTTP transport, for Azure SDK.

    Object is cached for performance.

    Returns a `AioHttpTransport` instance.
    """
    global _transport  # pylint: disable=global-statement
    if not _transport:
        # Azure SDK implements its own retry logic (e.g. for Cosmos DB), so we don't add it here
        _transport = AioHttpTransport(
            session_owner=False,  # Restrict the SDK to close the client after usage
            session=await aiohttp_session(),
        )
    return _transport


async def twilio_http() -> AsyncTwilioHttpClient:
    """
    Create a Twilio HTTP client.

    Object is cached for performance.

    Returns a `AsyncTwilioHttpClient` instance.
    """
    global _twilio_http  # pylint: disable=global-statement
    if not _twilio_http:
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
