import re
import time
from typing import Optional, Tuple
from config import (
    get_thread_ytdl,
    get_cached_stream,
    set_cached_stream,
    MEDIA_EXECUTOR,
    logger
)
from core.models import Song

def _extract_media_info(data: Optional[dict]) -> Tuple[Optional[str], Optional[str], int]:
    if not data:
        return None, None, 0
    entries = data.get('entries') if isinstance(data, dict) and 'entries' in data else [data]
    if not entries:
        entries = [data]
    for target in entries:
        if not target or not isinstance(target, dict):
            continue
        url = None
        if 'formats' in target and target['formats']:
            audio_formats = [
                f for f in target['formats']
                if f.get('url') and (
                    f.get('acodec') != 'none' or
                    f.get('vcodec') == 'none' or
                    'audio' in f.get('format', '').lower()
                )
            ]
            if audio_formats:
                best_audio = max(
                    audio_formats,
                    key=lambda f: (
                        1 if f.get('format_id') == '251' else 0,
                        1 if (f.get('ext') == 'webm' and f.get('acodec') == 'opus') else 0,
                        f.get('abr') or 0,
                        f.get('tbr') or 0
                    )
                )
                url = best_audio.get('url')
        if not url:
            url = target.get('url')
        if url:
            thumb = target.get('thumbnail', '')
            dur = target.get('duration', 0)
            return url, thumb, dur
    return None, None, 0

def extract_stream_url_sync(song_url: str, song_title: str) -> Tuple[Optional[str], Optional[str], int]:
    cache_key = song_url or song_title
    cached = get_cached_stream(cache_key)
    if cached:
        return cached

    stream_url, thumb, dur = None, None, 0

    if not ("spotify.com" in song_url or song_url.startswith("spotify:")):
        for attempt in range(2):
            try:
                ydl = get_thread_ytdl(extract_flat=False, noplaylist=True)
                data = ydl.extract_info(song_url, download=False)
                stream_url, thumb, dur = _extract_media_info(data)
                if stream_url:
                    break
            except Exception:
                if attempt == 0:
                    time.sleep(0.5)

    if not stream_url and song_title:
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', song_title).strip()
        yt_query = f"ytsearch5:{clean_title or song_title}"
        for attempt in range(2):
            try:
                ydl_yt = get_thread_ytdl(extract_flat=False, noplaylist=True)
                yt_data = ydl_yt.extract_info(yt_query, download=False)
                stream_url, thumb, dur = _extract_media_info(yt_data)
                if stream_url:
                    break
            except Exception:
                if attempt == 0:
                    time.sleep(0.5)

    if not stream_url and song_title:
        try:
            sc_query = f"scsearch3:{song_title}"
            ydl_sc = get_thread_ytdl(extract_flat=False, noplaylist=True)
            sc_data = ydl_sc.extract_info(sc_query, download=False)
            stream_url, thumb, dur = _extract_media_info(sc_data)
        except Exception:
            pass

    if stream_url:
        set_cached_stream(cache_key, stream_url, thumb, dur)
        return stream_url, thumb, dur

    return None, None, 0

def _fetch_autoplay_sync(last_title: str) -> Optional[dict]:
    try:
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', last_title).strip()
        clean_title = re.sub(r'(official\s+video|official\s+audio|mv|lyrics|feat\..*|ft\..*)', '', clean_title, flags=re.I).strip()
        query = f"ytsearch5:{clean_title} radio"
        ydl = get_thread_ytdl(extract_flat=True, noplaylist=True)
        info = ydl.extract_info(query, download=False)
        if info and 'entries' in info:
            for entry in info['entries']:
                if entry and entry.get('title'):
                    ent_title = entry.get('title', '')
                    if ent_title.lower() != last_title.lower() and clean_title.lower() not in ent_title.lower():
                        return entry
            for entry in info['entries']:
                if entry and entry.get('title') and entry.get('title').lower() != last_title.lower():
                    return entry
    except Exception as e:
        logger.error(f"[Autoplay Helper] {e}")
    return None

async def fetch_autoplay_recommendation(last_played: Song, loop) -> Optional[Song]:
    entry = await loop.run_in_executor(MEDIA_EXECUTOR, lambda: _fetch_autoplay_sync(last_played.title))
    if entry:
        return Song(entry, requester=last_played.requester)
    return None
