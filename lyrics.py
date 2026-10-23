import urllib.request
import urllib.parse
import json
import re
import time
from typing import Optional, Tuple, Dict, List, Any
from config import get_http_session, format_duration

_LYRICS_CACHE: Dict[str, Tuple[Tuple[str, str, bool], float]] = {}
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


def get_synced_lyrics_snippet(lrc_text: str, elapsed_sec: float, context_lines: int = 2) -> str:
    lines = parse_lrc_lines(lrc_text)
    if not lines:
        return ""
    active_idx = 0
    for idx, (ts, _) in enumerate(lines):
        if ts <= elapsed_sec + 0.5:
            active_idx = idx
        else:
            break
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
    active_idx = -1
    if elapsed_sec is not None and elapsed_sec >= 0:
        for idx, (ts, _) in enumerate(lines):
            if ts <= elapsed_sec + 0.5:
                active_idx = idx
            else:
                break
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
        async with session.get(url, headers=headers, timeout=timeout_sec) as resp:
            if resp.status == 200:
                body = await resp.text(encoding='utf-8', errors='ignore')
                return json.loads(body)
    except Exception:
        pass
    return None


async def fetch_lyrics_lrclib_async(track_name: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    clean_title = _clean_query_title(track_name)
    if artist_name:
        params = urllib.parse.urlencode({
            'track_name': clean_title,
            'artist_name': artist_name
        })
        url = f"https://lrclib.net/api/get?{params}"
        data = await _make_request_async(url)
        if isinstance(data, dict) and ('syncedLyrics' in data or 'plainLyrics' in data):
            text = data.get('syncedLyrics') or data.get('plainLyrics')
            if text and text.strip():
                display_name = f"{data.get('trackName', clean_title)} - {data.get('artistName', artist_name)}"
                is_synced = bool(data.get('syncedLyrics'))
                return display_name, text.strip(), is_synced

    search_q = f"{clean_title} {artist_name}".strip()
    params = urllib.parse.urlencode({'q': search_q})
    url = f"https://lrclib.net/api/search?{params}"
    data = await _make_request_async(url)
    if isinstance(data, list) and len(data) > 0:
        for item in data[:5]:
            if isinstance(item, dict) and item.get('syncedLyrics'):
                text = item['syncedLyrics'].strip()
                if text:
                    display_name = f"{item.get('trackName', clean_title)} - {item.get('artistName', artist_name)}"
                    return display_name, text, True
        for item in data[:3]:
            if isinstance(item, dict):
                text = item.get('plainLyrics')
                if text and text.strip():
                    display_name = f"{item.get('trackName', clean_title)} - {item.get('artistName', artist_name)}"
                    return display_name, text.strip(), False
    return None


async def fetch_lyrics_netease_async(track_name: str) -> Optional[Tuple[str, str, bool]]:
    try:
        clean_title = _clean_query_title(track_name)
        search_url = f"https://music.163.com/api/search/get/web?s={urllib.parse.quote(clean_title)}&type=1&offset=0&total=true&limit=1"
        search_res = await _make_request_async(search_url, timeout_sec=3.5)
        if isinstance(search_res, dict) and search_res.get('result', {}).get('songs'):
            song = search_res['result']['songs'][0]
            song_id = song.get('id')
            title = song.get('name', clean_title)
            artists = ", ".join(a.get('name', '') for a in song.get('artists', []))
            display_name = f"{title} - {artists}" if artists else title

            lyric_url = f"https://music.163.com/api/song/lyric?os=pc&id={song_id}&lv=-1&kv=-1&tv=-1"
            lyric_res = await _make_request_async(lyric_url, timeout_sec=3.5)
            if isinstance(lyric_res, dict):
                lrc = lyric_res.get('lrc', {}).get('lyric', '')
                if lrc and lrc.strip():
                    is_synced = bool(re.search(r'\[\d{2}:\d{2}', lrc))
                    return display_name, lrc.strip(), is_synced
    except Exception:
        pass
    return None


async def get_lyrics_async(track_title: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    cache_key = f"{track_title.strip().lower()}::{artist_name.strip().lower()}"
    now = time.time()
    cached = _LYRICS_CACHE.get(cache_key)
    if cached and (now - cached[1] < 3600):
        return cached[0]

    res = await fetch_lyrics_lrclib_async(track_title, artist_name)
    if not res:
        res = await fetch_lyrics_netease_async(track_title)

    if res:
        _LYRICS_CACHE[cache_key] = (res, now)
        if len(_LYRICS_CACHE) > 300:
            oldest = sorted(_LYRICS_CACHE.keys(), key=lambda k: _LYRICS_CACHE[k][1])[:60]
            for ok in oldest:
                _LYRICS_CACHE.pop(ok, None)
    return res


def _make_request(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 4.0) -> Optional[dict]:
    try:
        default_headers = {
            'User-Agent': 'DiscordMusicBot/2.0 (GitHub OpenSource Project; https://github.com/)'
        }
        if headers:
            default_headers.update(headers)
        req = urllib.request.Request(url, headers=default_headers, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode('utf-8', errors='ignore')
                return json.loads(body)
    except Exception:
        pass
    return None


def fetch_lyrics_lrclib(track_name: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    clean_title = _clean_query_title(track_name)
    if artist_name:
        params = urllib.parse.urlencode({
            'track_name': clean_title,
            'artist_name': artist_name
        })
        url = f"https://lrclib.net/api/get?{params}"
        data = _make_request(url)
        if data and ('syncedLyrics' in data or 'plainLyrics' in data):
            text = data.get('syncedLyrics') or data.get('plainLyrics')
            if text and text.strip():
                display_name = f"{data.get('trackName', clean_title)} - {data.get('artistName', artist_name)}"
                is_synced = bool(data.get('syncedLyrics'))
                return display_name, text.strip(), is_synced

    search_q = f"{clean_title} {artist_name}".strip()
    params = urllib.parse.urlencode({'q': search_q})
    url = f"https://lrclib.net/api/search?{params}"
    data = _make_request(url)
    if isinstance(data, list) and len(data) > 0:
        for item in data[:3]:
            text = item.get('syncedLyrics') or item.get('plainLyrics')
            if text and text.strip():
                display_name = f"{item.get('trackName', clean_title)} - {item.get('artistName', artist_name)}"
                is_synced = bool(item.get('syncedLyrics'))
                return display_name, text.strip(), is_synced
    return None


def fetch_lyrics_netease(track_name: str) -> Optional[Tuple[str, str, bool]]:
    try:
        clean_title = _clean_query_title(track_name)
        search_url = f"https://music.163.com/api/search/get/web?s={urllib.parse.quote(clean_title)}&type=1&offset=0&total=true&limit=1"
        search_res = _make_request(search_url, timeout=3.0)
        if search_res and search_res.get('result', {}).get('songs'):
            song = search_res['result']['songs'][0]
            song_id = song.get('id')
            title = song.get('name', clean_title)
            artists = ", ".join(a.get('name', '') for a in song.get('artists', []))
            display_name = f"{title} - {artists}" if artists else title

            lyric_url = f"https://music.163.com/api/song/lyric?os=pc&id={song_id}&lv=-1&kv=-1&tv=-1"
            lyric_res = _make_request(lyric_url, timeout=3.0)
            if lyric_res:
                lrc = lyric_res.get('lrc', {}).get('lyric', '')
                if lrc and lrc.strip():
                    is_synced = bool(re.search(r'\[\d{2}:\d{2}', lrc))
                    return display_name, lrc.strip(), is_synced
    except Exception:
        pass
    return None


def get_lyrics(track_title: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    cache_key = f"{track_title.strip().lower()}::{artist_name.strip().lower()}"
    now = time.time()
    cached = _LYRICS_CACHE.get(cache_key)
    if cached and (now - cached[1] < 3600):
        return cached[0]
    res = fetch_lyrics_lrclib(track_title, artist_name)
    if not res:
        res = fetch_lyrics_netease(track_title)
    if res:
        _LYRICS_CACHE[cache_key] = (res, now)
    return res
