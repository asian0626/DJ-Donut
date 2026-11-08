"""Shared aiohttp session (TLS verification stays ON)."""
from typing import Optional

import aiohttp

_HTTP_SESSION: Optional[aiohttp.ClientSession] = None


async def get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        _HTTP_SESSION = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _HTTP_SESSION


async def close_http_session() -> None:
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
    _HTTP_SESSION = None
