from aiohttp import ClientSession, DummyCookieJar, ClientTimeout
from aiohttp_retry import JitterRetry, RetryClient
from azure.core.pipeline.transport._aiohttp import AioHttpTransport
from twilio.http.async_http_client import AsyncTwilioHttpClient
from typing import Optional


_cookie_jar: Optional[DummyCookieJar] = None
_session: Optional[ClientSession] = None
_transport: Optional[AioHttpTransport] = None
_twilio_http: Optional[AsyncTwilioHttpClient] = None


async def _aiohttp_cookie_jar() -> DummyCookieJar:
    global _cookie_jar
    if not _cookie_jar:
        _cookie_jar = DummyCookieJar()
    return _cookie_jar


async def aiohttp_session() -> ClientSession:
    global _session
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
    global _transport
    if not _transport:
        _transport = AioHttpTransport(
            session_owner=False,  # Restrict the SDK to close the client after usage
            session=await aiohttp_session(),
        )
    return _transport


async def twilio_http() -> AsyncTwilioHttpClient:
    global _twilio_http
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
            ),
        )
    return _twilio_http
