import json
import re
import urllib.parse
from typing import Any, List, Optional, Tuple

import aiohttp

from config import format_duration, get_cached_lyrics, get_http_session, logger, set_cached_lyrics

_LRC_LINE_REGEX = re.compile(r'\[(\d{1,3}):(\d{2})(?:[\.:](\d{1,3}))?\](.*)')


def _clean_query_title(track_name: str) -> str:
    clean_title = re.sub(r'[\(\[\{\【\「\『\《\（\［].*?[\)\]\}\】\」\』\》\）\］]', '', track_name).strip()
    clean_title = re.sub(r'(official\s+video|official\s+audio|mv|lyrics|lyric\s+video|feat\..*|ft\..*|動態歌詞|高音質|完整版|hd|4k)', '', clean_title, flags=re.I).strip()
    return clean_title if clean_title else track_name.strip()


def parse_lrc_lines(lrc_text: str) -> List[Tuple[float, str]]:
    parsed: List[Tuple[float, str]] = []
    if not lrc_text:
        return parsed
    for raw_line in lrc_text.splitlines():
        m = _LRC_LINE_REGEX.match(raw_line.strip())
        if m:
            mins = int(m.group(1))
            secs = int(m.group(2))
            ms_str = m.group(3) or "0"
            frac = int(ms_str) / (10 ** len(ms_str))
            total_sec = mins * 60.0 + secs + frac
            content = m.group(4).strip()
            if content:
                parsed.append((total_sec, content))
    parsed.sort(key=lambda x: x[0])
    return parsed


def _find_active_lrc_index(lines: List[Tuple[float, str]], elapsed_sec: float) -> int:
    active_idx = 0
    for idx, (ts, _) in enumerate(lines):
        if ts <= elapsed_sec + 0.5:
            active_idx = idx
        else:
            break
    return active_idx


def get_synced_lyrics_snippet(lrc_text: str, elapsed_sec: float, context_lines: int = 2) -> str:
    lines = parse_lrc_lines(lrc_text)
    if not lines:
        return ""
    active_idx = _find_active_lrc_index(lines, elapsed_sec)
    start_idx = max(0, active_idx - context_lines)
    end_idx = min(len(lines), active_idx + context_lines + 1)
    out = []
    for idx in range(start_idx, end_idx):
        ts, text = lines[idx]
        time_badge = f"`{format_duration(int(ts))}`"
        if idx == active_idx:
            out.append(f"▶️ {time_badge} **{text}** 🎵")
        else:
            out.append(f"　 {time_badge} *{text}*")
    return "\n".join(out)


def format_clean_lyrics(lrc_text: str, is_synced: bool, elapsed_sec: Optional[float] = None) -> str:
    if not is_synced:
        return lrc_text.strip()
    lines = parse_lrc_lines(lrc_text)
    if not lines:
        return lrc_text.strip()
    active_idx = _find_active_lrc_index(lines, elapsed_sec) if (elapsed_sec is not None and elapsed_sec >= 0) else -1
    formatted = []
    for idx, (ts, text) in enumerate(lines):
        time_badge = f"`[{format_duration(int(ts))}]`"
        if idx == active_idx:
            formatted.append(f"▶️ {time_badge} **{text}** 🎤")
        else:
            formatted.append(f"{time_badge} {text}")
    return "\n".join(formatted)


async def _make_request_async(url: str, timeout_sec: float = 4.0) -> Optional[Any]:
    try:
        session = await get_http_session()
        headers = {
            'User-Agent': 'DiscordMusicBot/2.0 (GitHub OpenSource Project; https://github.com/)'
        }
        req_timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with session.get(url, headers=headers, timeout=req_timeout) as resp:
            if resp.status == 200:
                body = await resp.text(encoding='utf-8', errors='ignore')
                return json.loads(body)
    except Exception:
        logger.debug("Ignored exception in _make_request_async", exc_info=True)
    return None


def _extract_lrclib_entry(item: dict, clean_title: str, artist_name: str, prefer_synced: bool = True) -> Optional[Tuple[str, str, bool]]:
    key = 'syncedLyrics' if prefer_synced else 'plainLyrics'
    raw = item.get(key)
    if isinstance(raw, str) and raw.strip():
        display_name = f"{item.get('trackName', clean_title)} - {item.get('artistName', artist_name)}"
        return display_name, raw.strip(), prefer_synced
    return None


def _search_lrclib_items(
    items: list, clean_title: str, artist_name: str, prefer_synced: bool, limit: int
) -> Optional[Tuple[str, str, bool]]:
    for item in items[:limit]:
        if isinstance(item, dict):
            res = _extract_lrclib_entry(item, clean_title, artist_name, prefer_synced)
            if res:
                return res
    return None


async def fetch_lyrics_lrclib_async(track_name: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    clean_title = _clean_query_title(track_name)
    if artist_name:
        params = urllib.parse.urlencode({'track_name': clean_title, 'artist_name': artist_name})
        data = await _make_request_async(f"https://lrclib.net/api/get?{params}")
        if isinstance(data, dict):
            res = _extract_lrclib_entry(data, clean_title, artist_name, True) or _extract_lrclib_entry(
                data, clean_title, artist_name, False
            )
            if res:
                return res

    params = urllib.parse.urlencode({'q': f"{clean_title} {artist_name}".strip()})
    data = await _make_request_async(f"https://lrclib.net/api/search?{params}")
    if isinstance(data, list):
        return _search_lrclib_items(data, clean_title, artist_name, True, 5) or _search_lrclib_items(
            data, clean_title, artist_name, False, 3
        )
    return None


async def fetch_lyrics_netease_async(track_name: str) -> Optional[Tuple[str, str, bool]]:
    try:
        clean_title = _clean_query_title(track_name)
        search_url = f"https://music.163.com/api/search/get/web?s={urllib.parse.quote(clean_title)}&type=1&offset=0&total=true&limit=1"
        search_res = await _make_request_async(search_url, timeout_sec=3.5)
        songs = search_res.get('result', {}).get('songs') if isinstance(search_res, dict) else None
        if not songs:
            return None
        song = songs[0]
        song_id = song.get('id')
        title = song.get('name', clean_title)
        artists = ", ".join(a.get('name', '') for a in song.get('artists', []))
        display_name = f"{title} - {artists}" if artists else title

        lyric_url = f"https://music.163.com/api/song/lyric?os=pc&id={song_id}&lv=-1&kv=-1&tv=-1"
        lyric_res = await _make_request_async(lyric_url, timeout_sec=3.5)
        lrc = lyric_res.get('lrc', {}).get('lyric', '') if isinstance(lyric_res, dict) else ''
        if lrc and lrc.strip():
            is_synced = bool(re.search(r'\[\d{2}:\d{2}', lrc))
            return display_name, lrc.strip(), is_synced
    except Exception:
        logger.debug("Ignored exception in fetch_lyrics_netease_async", exc_info=True)
    return None


async def get_lyrics_async(track_title: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    cache_key = f"{track_title.strip().lower()}::{artist_name.strip().lower()}"
    cached = get_cached_lyrics(cache_key)
    if cached is not None:
        return cached

    res = await fetch_lyrics_lrclib_async(track_title, artist_name)
    if not res:
        res = await fetch_lyrics_netease_async(track_title)

    if res:
        set_cached_lyrics(cache_key, res)
    return res
