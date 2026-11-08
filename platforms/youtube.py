import re
import time
from typing import Any, List, Optional, Tuple

from config import (
    get_cached_stream,
    get_http_session,
    get_thread_ytdl,
    logger,
    run_media,
    set_cached_stream,
    stream_cache_key,
)
from metrics import timed_extraction
from settings import LASTFM_API_KEY

_AUDIO_EXTS = ('webm', 'm4a', 'opus', 'mp3', 'aac', 'ogg', 'wav')
_NOISE_RE = re.compile(r'(official\s+video|official\s+audio|mv|lyrics|feat\..*|ft\..*)', re.I)


def _is_audio_format(fmt: dict) -> bool:
    if not fmt.get('url'):
        return False
    acodec = fmt.get('acodec')
    if acodec and acodec != 'none':
        return True
    if 'audio' in fmt.get('format', '').lower():
        return True
    return fmt.get('ext') in _AUDIO_EXTS and acodec != 'none'


def _pick_best_audio_url(formats: list) -> Optional[str]:
    audio_formats = [f for f in formats if isinstance(f, dict) and _is_audio_format(f)]
    if not audio_formats:
        return None
    best = max(
        audio_formats,
        key=lambda f: (
            1 if f.get('format_id') == '251' else 0,
            1 if (f.get('ext') == 'webm' and f.get('acodec') == 'opus') else 0,
            f.get('abr') or 0,
            f.get('tbr') or 0,
        ),
    )
    return best.get('url')


def _extract_media_info(data: Optional[dict]) -> Tuple[Optional[str], Optional[str], int]:
    if not data:
        return None, None, 0
    entries = data.get('entries') if isinstance(data, dict) else None
    targets = entries if entries else [data]
    for target in targets:
        if not isinstance(target, dict):
            continue
        formats = target.get('formats')
        url = _pick_best_audio_url(formats) if formats else None
        url = url or target.get('url')
        if url:
            return url, target.get('thumbnail', ''), int(target.get('duration', 0) or 0)
    return None, None, 0


def _try_extract_with_retries(query: str, backoffs: List[float], label: str) -> Tuple[Optional[str], Optional[str], int]:
    for attempt, delay in enumerate(backoffs):
        try:
            ydl = get_thread_ytdl(extract_flat=False, noplaylist=True)
            data = ydl.extract_info(query, download=False)
            stream_url, thumb, dur = _extract_media_info(data)
            if stream_url:
                return stream_url, thumb, dur
        except Exception as e:
            logger.debug(f"[{label}] {e}")
            if attempt < len(backoffs) - 1:
                time.sleep(delay)
    return None, None, 0


def _try_extract_once(query: str, label: str) -> Tuple[Optional[str], Optional[str], int]:
    try:
        ydl = get_thread_ytdl(extract_flat=False, noplaylist=True)
        data = ydl.extract_info(query, download=False)
        return _extract_media_info(data)
    except Exception as e:
        logger.debug(f"[{label}] {e}")
        return None, None, 0


@timed_extraction
def extract_stream_url_sync(song_url: str, song_title: str) -> Tuple[Optional[str], Optional[str], int]:
    cache_key = stream_cache_key(song_url, song_title)
    cached = get_cached_stream(cache_key)
    if cached:
        return cached

    stream_url, thumb, dur = None, None, 0
    backoffs = [0.3, 0.8]

    if song_url and not ("spotify.com" in song_url or song_url.startswith("spotify:")):
        stream_url, thumb, dur = _try_extract_with_retries(song_url, backoffs, "YTDL URL Extract Error")

    if not stream_url and song_title:
        stream_url, thumb, dur = _try_extract_with_retries(f"ytsearch5:{song_title}", backoffs, "YTDL Title Search Error")

    if not stream_url and song_title:
        clean_title = re.sub(r'[\(\[\{\【\「\『\《\（\［].*?[\)\]\}\】\」\』\》\）\］]', '', song_title).strip()
        clean_title = _NOISE_RE.sub('', clean_title).strip()
        if clean_title and clean_title != song_title:
            stream_url, thumb, dur = _try_extract_once(f"ytsearch5:{clean_title}", "YTDL Clean Search Error")

    if not stream_url and song_title:
        stream_url, thumb, dur = _try_extract_once(f"scsearch3:{song_title}", "SC Search Error")

    if stream_url:
        set_cached_stream(cache_key, stream_url, thumb, dur)
        return stream_url, thumb, dur

    return None, None, 0


def _clean_title(title: str) -> str:
    """Strip bracketed text and common noise words from a video title."""
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', title).strip()
    return _NOISE_RE.sub('', cleaned).strip()


def _search_first_entry_sync(query: str) -> Optional[dict]:
    try:
        info = get_thread_ytdl(extract_flat=True, noplaylist=True).extract_info(f"ytsearch1:{query}", download=False)
    except Exception as exc:
        logger.debug("[Autoplay Search] %s", exc)
        return None
    entries = (info or {}).get('entries') or []
    return entries[0] if entries and entries[0] else None


async def _fetch_lastfm_recommendation(last_played: Any, history_titles: Optional[list]) -> Optional[dict]:
    """Ask Last.fm for similar tracks (only used when ``LASTFM_API_KEY`` is set)."""
    params = {
        "method": "track.getsimilar",
        "track": _clean_title(last_played.title),
        "artist": (getattr(last_played, "uploader", "") or "").replace(" - Topic", "").strip(),
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 10,
        "autocorrect": 1,
    }
    try:
        session = await get_http_session()
        async with session.get("https://ws.audioscrobbler.com/2.0/", params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except Exception as exc:
        logger.debug("[Last.fm] request failed: %s", exc)
        return None
    tracks = (data.get("similartracks") or {}).get("track") or []
    seen = {h.lower().strip() for h in (history_titles or []) if h}
    for tr in tracks:
        name = (tr.get("name") or "").strip()
        artist = ((tr.get("artist") or {}).get("name") or "").strip()
        if not name or any(name.lower() in h for h in seen):
            continue
        query_str = f"{name} {artist}".strip()
        entry = await run_media(lambda q=query_str: _search_first_entry_sync(q))
        if entry:
            return entry
    return None


def _pick_autoplay_entry(entries: list, last_title: str, clean_title: str, hist_set: set) -> Optional[dict]:
    lower_last = last_title.lower()
    lower_clean = clean_title.lower()
    for entry in entries:
        if entry and entry.get('title'):
            lower_ent = entry.get('title', '').strip().lower()
            if lower_ent != lower_last and lower_clean not in lower_ent and lower_ent not in hist_set:
                return entry
    for entry in entries:
        if entry and entry.get('title'):
            lower_ent = entry.get('title', '').strip().lower()
            if lower_ent != lower_last and lower_ent not in hist_set:
                return entry
    return None


def _fetch_autoplay_sync(last_title: str, history_titles: Optional[list] = None) -> Optional[dict]:
    try:
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', last_title).strip()
        if not clean_title:
            clean_title = last_title
        hist_set = {h.lower().strip() for h in (history_titles or []) if h}
        ydl = get_thread_ytdl(extract_flat=True, noplaylist=True)
        info = ydl.extract_info(f"ytsearch10:{clean_title} mix", download=False)
        entries = info.get('entries') if isinstance(info, dict) else None
        if entries:
            return _pick_autoplay_entry(entries, last_title, clean_title, hist_set)
    except Exception as e:
        logger.error(f"[Autoplay Helper] {e}")
    return None


async def fetch_autoplay_recommendation(last_played: Any, loop: Any = None, history_titles: Optional[list] = None) -> Optional[Any]:
    entry = None
    if LASTFM_API_KEY:
        entry = await _fetch_lastfm_recommendation(last_played, history_titles)
    if not entry:
        last_title = last_played.title
        entry = await run_media(lambda lt=last_title, ht=history_titles: _fetch_autoplay_sync(lt, history_titles=ht))
    if entry:
        from core.models import Song
        return Song(entry, requester=last_played.requester)
    return None
