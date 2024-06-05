from aiohttp import ClientSession, DummyCookieJar
from azure.core.pipeline.transport._aiohttp import AioHttpTransport
from typing import Optional


_cookie_jar: Optional[DummyCookieJar] = None
_session: Optional[ClientSession] = None
_transport: Optional[AioHttpTransport] = None


async def _aiohttp_cookie_jar() -> DummyCookieJar:
    global _cookie_jar
    if not _cookie_jar:
        _cookie_jar = DummyCookieJar()
    return _cookie_jar


async def aiohttp_session() -> ClientSession:
    global _session
    if not _session:
        _session = ClientSession(
            auto_decompress=False,
            cookie_jar=await _aiohttp_cookie_jar(),
            trust_env=True,
        )
    return _session


async def azure_transport() -> AioHttpTransport:
    global _transport
    if not _transport:
        _transport = AioHttpTransport(
            session_owner=False,
            session=await aiohttp_session(),
        )
    return _transport
