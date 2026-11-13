"""In-memory caches for resolved stream URLs, platform metadata, lyrics, and autocomplete.

All caches are LRU + TTL bounded (``cachetools.TTLCache``) and guarded by locks.
"""
import re
import threading
import time
from typing import List, Optional, Tuple

from cachetools import TTLCache

STREAM_TTL = 600
METADATA_TTL = 3600
LYRICS_TTL = 3600
AUTOCOMPLETE_TTL = 120

_STREAM_CACHE: "TTLCache[str, Tuple[float, Tuple[str, Optional[str], int]]]" = TTLCache(maxsize=500, ttl=365 * 24 * 3600)
_STREAM_LOCK = threading.Lock()
_METADATA_CACHE: "TTLCache[str, Tuple[float, dict]]" = TTLCache(maxsize=1000, ttl=365 * 24 * 3600)
_METADATA_LOCK = threading.Lock()
_LYRICS_CACHE: "TTLCache[str, Tuple[str, str, bool]]" = TTLCache(maxsize=300, ttl=LYRICS_TTL)
_LYRICS_LOCK = threading.Lock()
_AUTOCOMPLETE_CACHE: "TTLCache[str, List[str]]" = TTLCache(maxsize=500, ttl=AUTOCOMPLETE_TTL)
_AUTOCOMPLETE_LOCK = threading.Lock()

_EXPIRE_RE = re.compile(r'expire=(\d+)')


def is_stream_url_expired(url: str) -> bool:
    """True when ``url`` is empty or carries an ``expire=<epoch>`` already (nearly) past."""
    if not url:
        return True
    m = _EXPIRE_RE.search(url)
    if m:
        try:
            return time.time() >= (int(m.group(1)) - 60)
        except ValueError:
            return False
    return False


def stream_cache_key(url: str = "", title: str = "") -> str:
    """Canonical cache key: the song URL when known, otherwise the normalized title."""
    url = (url or "").strip()
    if url:
        return url
    return f"title:{(title or '').strip().lower()}"


def get_cached_stream(key: str) -> Optional[Tuple[str, Optional[str], int]]:
    with _STREAM_LOCK:
        cached = _STREAM_CACHE.get(key)
        if cached is None:
            return None
        expires_at, entry = cached
        if time.monotonic() >= expires_at or is_stream_url_expired(entry[0]):
            _STREAM_CACHE.pop(key, None)
            return None
        return entry


def invalidate_cached_stream(key: str) -> None:
    with _STREAM_LOCK:
        _STREAM_CACHE.pop(key, None)


def set_cached_stream(key: str, stream_url: str, thumb: Optional[str], dur: int, ttl: Optional[int] = None) -> None:
    lifetime = STREAM_TTL if ttl is None else max(0, ttl)
    with _STREAM_LOCK:
        if lifetime == 0:
            _STREAM_CACHE.pop(key, None)
            return
        _STREAM_CACHE[key] = (time.monotonic() + lifetime, (stream_url, thumb, dur))


def get_cached_metadata(key: str) -> Optional[dict]:
    with _METADATA_LOCK:
        cached = _METADATA_CACHE.get(key)
        if cached is None:
            return None
        expires_at, data = cached
        if time.monotonic() >= expires_at:
            _METADATA_CACHE.pop(key, None)
            return None
        return data


def set_cached_metadata(key: str, data: dict, ttl: Optional[int] = None) -> None:
    lifetime = METADATA_TTL if ttl is None else max(0, ttl)
    with _METADATA_LOCK:
        if lifetime == 0:
            _METADATA_CACHE.pop(key, None)
            return
        _METADATA_CACHE[key] = (time.monotonic() + lifetime, data)


def get_cached_lyrics(key: str) -> Optional[Tuple[str, str, bool]]:
    with _LYRICS_LOCK:
        return _LYRICS_CACHE.get(key)


def set_cached_lyrics(key: str, val: Tuple[str, str, bool]) -> None:
    with _LYRICS_LOCK:
        _LYRICS_CACHE[key] = val


def get_cached_autocomplete(key: str) -> Optional[List[str]]:
    with _AUTOCOMPLETE_LOCK:
        return _AUTOCOMPLETE_CACHE.get(key)


def set_cached_autocomplete(key: str, suggestions: List[str]) -> None:
    with _AUTOCOMPLETE_LOCK:
        _AUTOCOMPLETE_CACHE[key] = suggestions


def clear_caches() -> None:
    with _STREAM_LOCK:
        _STREAM_CACHE.clear()
    with _METADATA_LOCK:
        _METADATA_CACHE.clear()
    with _LYRICS_LOCK:
        _LYRICS_CACHE.clear()
    with _AUTOCOMPLETE_LOCK:
        _AUTOCOMPLETE_CACHE.clear()
