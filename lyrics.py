# -*- coding: utf-8 -*-
"""
Lyrics Retrieval Engine for Discord Music Bot
Fetches synchronized and plain lyrics via LRCLIB (open-source public database)
with automatic fallback to Netease Cloud Music.
Zero API-key required!
"""

import urllib.request
import urllib.parse
import json
import ssl
import re
from typing import Optional, Tuple, Dict, Any


def _make_request(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 4.0) -> Optional[dict]:
    """Internal helper to fetch JSON via urllib with SSL verification bypass if needed"""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        default_headers = {
            'User-Agent': 'DiscordMusicBot/2.0 (GitHub OpenSource Project; https://github.com/)'
        }
        if headers:
            default_headers.update(headers)

        req = urllib.request.Request(url, headers=default_headers, method='GET')
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode('utf-8', errors='ignore')
                return json.loads(body)
    except Exception:
        pass
    return None


def fetch_lyrics_lrclib(track_name: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    """
    Fetches lyrics from LRCLIB (https://lrclib.net).
    Returns: (title_display, lyrics_text, is_synced)
    """
    clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', track_name).strip()
    clean_title = re.sub(r'(official\s+video|official\s+audio|mv|lyrics|feat\..*|ft\..*)', '', clean_title, flags=re.I).strip()
    if not clean_title:
        clean_title = track_name

    # 1. Exact match attempt
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

    # 2. Search query attempt
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
    """
    Fallback lyrics engine via Netease Cloud Music (excellent for Chinese, Japanese, Korean tracks).
    """
    try:
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', track_name).strip()
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
                    # Check if synced
                    is_synced = bool(re.search(r'\[\d{2}:\d{2}', lrc))
                    return display_name, lrc.strip(), is_synced
    except Exception:
        pass
    return None


def get_lyrics(track_title: str, artist_name: str = "") -> Optional[Tuple[str, str, bool]]:
    """
    Unified entry point for retrieving lyrics.
    Tries LRCLIB first, then falls back to Netease.
    """
    res = fetch_lyrics_lrclib(track_title, artist_name)
    if res:
        return res

    res = fetch_lyrics_netease(track_title)
    if res:
        return res

    return None
