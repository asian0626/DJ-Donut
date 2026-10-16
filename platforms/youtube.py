import re
import time
from typing import Optional, Tuple, Any
from config import (
    get_thread_ytdl,
    get_cached_stream,
    set_cached_stream,
    MEDIA_EXECUTOR,
    logger
)

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
                    (f.get('acodec') and f.get('acodec') != 'none') or
                    ('audio' in f.get('format', '').lower()) or
                    (f.get('ext') in ('webm', 'm4a', 'opus', 'mp3', 'aac', 'ogg', 'wav') and f.get('acodec') != 'none')
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
    if song_url:
        cached = get_cached_stream(song_url)
        if cached:
            return cached
    if song_title:
        cached = get_cached_stream(song_title)
        if cached:
            return cached

    stream_url, thumb, dur = None, None, 0
    backoffs = [0.5, 1.2, 2.5]

    if song_url and not ("spotify.com" in song_url or song_url.startswith("spotify:")):
        for attempt, delay in enumerate(backoffs):
            try:
                ydl = get_thread_ytdl(extract_flat=False, noplaylist=True)
                data = ydl.extract_info(song_url, download=False)
                stream_url, thumb, dur = _extract_media_info(data)
                if stream_url:
                    break
            except Exception:
                if attempt < len(backoffs) - 1:
                    time.sleep(delay)

    if not stream_url and song_title:
        for attempt, delay in enumerate(backoffs):
            try:
                ydl_yt = get_thread_ytdl(extract_flat=False, noplaylist=True)
                yt_data = ydl_yt.extract_info(f"ytsearch5:{song_title}", download=False)
                stream_url, thumb, dur = _extract_media_info(yt_data)
                if stream_url:
                    break
            except Exception:
                if attempt < len(backoffs) - 1:
                    time.sleep(delay)

    if not stream_url and song_title:
        clean_title = re.sub(r'[\(\[\{\【\「\『\《\（\［].*?[\)\]\}\】\」\』\》\）\］]', '', song_title).strip()
        clean_title = re.sub(r'(official\s+video|official\s+audio|mv|lyrics|feat\..*|ft\..*)', '', clean_title, flags=re.I).strip()
        if clean_title and clean_title != song_title:
            try:
                ydl_yt2 = get_thread_ytdl(extract_flat=False, noplaylist=True)
                yt_data2 = ydl_yt2.extract_info(f"ytsearch5:{clean_title}", download=False)
                stream_url, thumb, dur = _extract_media_info(yt_data2)
            except Exception:
                pass

    if not stream_url and song_title:
        try:
            sc_query = f"scsearch3:{song_title}"
            ydl_sc = get_thread_ytdl(extract_flat=False, noplaylist=True)
            sc_data = ydl_sc.extract_info(sc_query, download=False)
            stream_url, thumb, dur = _extract_media_info(sc_data)
        except Exception:
            pass

    if stream_url:
        if song_url:
            set_cached_stream(song_url, stream_url, thumb, dur)
        if song_title:
            set_cached_stream(song_title, stream_url, thumb, dur)
        return stream_url, thumb, dur

    return None, None, 0

def _fetch_autoplay_sync(last_title: str, history_titles: Optional[list] = None) -> Optional[dict]:
    try:
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', last_title).strip()
        clean_title = re.sub(r'(official\s+video|official\s+audio|mv|lyrics|feat\..*|ft\..*)', '', clean_title, flags=re.I).strip()
        query = f"ytsearch5:{clean_title} radio"
        ydl = get_thread_ytdl(extract_flat=True, noplaylist=True)
        info = ydl.extract_info(query, download=False)
        hist_set = {h.lower().strip() for h in (history_titles or []) if h}
        if info and 'entries' in info:
            for entry in info['entries']:
                if entry and entry.get('title'):
                    ent_title = entry.get('title', '').strip()
                    lower_ent = ent_title.lower()
                    if lower_ent != last_title.lower() and clean_title.lower() not in lower_ent and lower_ent not in hist_set:
                        return entry
            for entry in info['entries']:
                if entry and entry.get('title'):
                    ent_title = entry.get('title', '').strip()
                    lower_ent = ent_title.lower()
                    if lower_ent != last_title.lower() and lower_ent not in hist_set:
                        return entry
    except Exception as e:
        logger.error(f"[Autoplay Helper] {e}")
    return None

async def fetch_autoplay_recommendation(last_played: Any, loop, history_titles: Optional[list] = None) -> Optional[Any]:
    entry = await loop.run_in_executor(MEDIA_EXECUTOR, lambda: _fetch_autoplay_sync(last_played.title, history_titles=history_titles))
    if entry:
        from core.models import Song
        return Song(entry, requester=last_played.requester)
    return None
