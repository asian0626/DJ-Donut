import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import os
import time
import subprocess
from collections import deque
from typing import Dict, Optional, List, Tuple
import yt_dlp
from dotenv import load_dotenv
import json
import re
import urllib.request
import urllib.parse
import ssl

from i18n import t, GUILD_LANGUAGES, SUPPORTED_LANGUAGES, resolve_locale
from lyrics import get_lyrics

load_dotenv()

__version__ = "1.0.4"

try:
    import wavelink
    HAS_WAVELINK = True
except ImportError:
    wavelink = None
    HAS_WAVELINK = False

LAVALINK_URI = os.getenv("LAVALINK_URI", "").strip()
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass").strip()

TOKENS = []
for i in range(1, 6):
    t_val = os.getenv(f"BOT_TOKEN_{i}")
    if t_val and t_val.strip() and t_val != "YOUR_BOT_TOKEN_HERE" and not t_val.startswith("YOUR_BOT_TOKEN_"):
        TOKENS.append((i, t_val.strip()))

if not TOKENS:
    legacy = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
    if legacy and legacy.strip() and legacy != "YOUR_BOT_TOKEN_HERE" and not legacy.startswith("YOUR_BOT_TOKEN_"):
        TOKENS.append((1, legacy.strip()))

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")

BASSBOOST_PRESETS = {
    "off": {"label": "❌ 關閉", "db": 0, "ffmpeg": None, "gain": 0.0},
    "low": {"label": "🍃 輕微低音 (+4dB)", "db": 4, "ffmpeg": "bass=g=4:f=110:w=0.6", "gain": 0.15},
    "medium": {"label": "🔊 飽滿重低音 (+8dB)", "db": 8, "ffmpeg": "bass=g=8:f=110:w=0.6", "gain": 0.28},
    "high": {"label": "💥 狂暴重低音 (+14dB)", "db": 14, "ffmpeg": "bass=g=14:f=100:w=0.6", "gain": 0.42},
    "extreme": {"label": "🔥 震動全場 (+20dB)", "db": 20, "ffmpeg": "bass=g=20:f=90:w=0.7", "gain": 0.65}
}

EQ_PRESETS = {
    "reset": {"label": "🎵 原音直通 (Flat)", "ffmpeg": None},
    "bass": {"label": "🔊 重低音強化", "ffmpeg": "bass=g=8:f=100:w=0.6"},
    "rock": {"label": "🎸 搖滾動態 (V型強化)", "ffmpeg": "equalizer=f=64:t=q:w=1:g=6,equalizer=f=12000:t=q:w=1:g=5"},
    "pop": {"label": "🎤 流行樂 (人聲溫暖)", "ffmpeg": "equalizer=f=1000:t=q:w=1:g=4,equalizer=f=2500:t=q:w=1:g=3"},
    "vocal": {"label": "✨ 清澈人聲 (凸顯主唱)", "ffmpeg": "highpass=f=120,equalizer=f=2000:t=q:w=1:g=5"},
    "treble": {"label": "💎 通透高音 (空靈感)", "ffmpeg": "treble=g=8:f=8000:w=0.6"},
    "nightcore": {"label": "⚡ 夜核 Nightcore (加速升調)", "ffmpeg": "asetrate=48000*1.25,aresample=48000"},
    "vaporwave": {"label": "🌊 蒸氣波 Vaporwave (慢速復古)", "ffmpeg": "asetrate=48000*0.85,aresample=48000"},
    "8d": {"label": "🎧 8D 空間環繞立體聲", "ffmpeg": "apulsator=hz=0.125"}
}

class YTDLNullLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

def get_ytdl_opts(extract_flat: bool = False, noplaylist: bool = True) -> dict:
    opts = {
        'format': 'bestaudio[ext=webm][acodec=opus][abr>=160]/bestaudio[format_id=251]/bestaudio/best',
        'extractaudio': True,
        'audioquality': '0',
        'restrictfilenames': True,
        'noplaylist': noplaylist,
        'nocheckcertificate': True,
        'ignoreerrors': True,
        'quiet': True,
        'no_warnings': True,
        'logger': YTDLNullLogger(),
        'default_search': 'ytsearch',
        'source_address': '0.0.0.0',
        'socket_timeout': 10,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web', 'mweb', 'ios'],
                'skip': ['hls', 'dash']
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        }
    }
    if extract_flat:
        opts['extract_flat'] = True
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE
    return opts

def get_ffmpeg_options(state: 'GuildMusicState') -> dict:
    before = '-nostats -loglevel fatal -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx -probesize 32k -analyzeduration 0'
    filters = []
    bb = BASSBOOST_PRESETS.get(state.bassboost, {}).get("ffmpeg")
    if bb:
        filters.append(bb)
    eq = EQ_PRESETS.get(state.filter, {}).get("ffmpeg")
    if eq:
        filters.append(eq)

    if filters:
        opt = f"-threads 1 -vn -af \"{','.join(filters)}\""
    else:
        opt = "-threads 1 -vn"
    return {'before_options': before, 'options': opt}

def format_duration(seconds: Optional[int], lang: str = 'zh-TW') -> str:
    if not seconds:
        return "Live / Unknown" if lang == 'en' else "直播 / 未知"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"

class Song:
    def __init__(self, data: dict, requester: discord.Member, wavelink_track=None):
        self.data = data
        self.title = data.get('title', '未知標題')
        self.url = data.get('webpage_url') or data.get('url', '')
        self.stream_url = data.get('url', '')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', '未知上傳者')
        self.requester = requester
        self.wavelink_track = wavelink_track

class GuildMusicState:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue = deque()
        self.current: Optional[Song] = None
        self.last_played: Optional[Song] = None
        self.loop_mode: str = 'off'
        self.volume: float = 1.0
        self.text_channel: Optional[discord.TextChannel] = None
        self.is_playing = False
        self.bassboost: str = 'off'
        self.filter: str = 'reset'
        self.active_engine: str = 'Native'
        self.autoplay: bool = False
        self.mode_247: bool = True
        self.lang: Optional[str] = None
        self.start_time: float = 0.0
        self.paused_duration: float = 0.0
        self.pause_timestamp: float = 0.0

def _extract_media_info(data: Optional[dict]) -> Tuple[Optional[str], Optional[str], int]:
    if not data:
        return None, None, 0
    entries = data.get('entries') if isinstance(data, dict) and 'entries' in data else [data]
    if not entries:
        entries = [data]
    for target in entries:
        if not target or not isinstance(target, dict):
            continue
        url = target.get('url')
        if not url and 'formats' in target and target['formats']:
            audio_formats = [f for f in target['formats'] if isinstance(f, dict) and f.get('acodec') != 'none' and f.get('url')]
            if audio_formats:
                best_f = max(audio_formats, key=lambda f: (1 if f.get('acodec') == 'opus' else 0, f.get('abr') or 0))
                url = best_f.get('url')
        if url:
            thumb = target.get('thumbnail', '')
            dur = target.get('duration', 0)
            return url, thumb, dur
    return None, None, 0

def extract_stream_url_sync(song_url: str, song_title: str) -> Tuple[Optional[str], Optional[str], int]:
    if not ("spotify.com" in song_url or song_url.startswith("spotify:")):
        try:
            ydl = yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True))
            data = ydl.extract_info(song_url, download=False)
            stream_url, thumb, dur = _extract_media_info(data)
            if stream_url:
                return stream_url, thumb, dur
        except Exception:
            pass

    if song_title:
        try:
            clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', song_title).strip()
            yt_query = f"ytsearch5:{clean_title or song_title}"
            ydl_yt = yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True))
            yt_data = ydl_yt.extract_info(yt_query, download=False)
            stream_url, thumb, dur = _extract_media_info(yt_data)
            if stream_url:
                return stream_url, thumb, dur
        except Exception:
            pass

        try:
            sc_query = f"scsearch3:{song_title}"
            ydl_sc = yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True))
            sc_data = ydl_sc.extract_info(sc_query, download=False)
            stream_url, thumb, dur = _extract_media_info(sc_data)
            if stream_url:
                return stream_url, thumb, dur
        except Exception:
            pass

    return None, None, 0


SPOTIFY_URL_REGEX = re.compile(
    r'(?:https?://(?:open\.)?spotify\.com/(?:intl-[a-zA-Z-]+/)?|spotify:)(track|playlist|album|artist)[:/]([a-zA-Z0-9]+)'
)

NETEASE_URL_REGEX = re.compile(
    r'(?:https?://)?(?:[a-zA-Z0-9-]+\.)?(?:music\.163\.com|163cn\.tv|163\.lu)/.*?(\b(?:song|playlist|album)\b).*?(?:id=|\/)(\d+)',
    re.IGNORECASE
)

APPLE_MUSIC_URL_REGEX = re.compile(
    r'(?:https?://)?music\.apple\.com/(?:([a-z]{2})/)?(album|playlist|song)/([^/?#]+)(?:/(\d+|pl\.[a-zA-Z0-9]+))?(?:\?.*?\bi=(\d+))?',
    re.IGNORECASE
)

_spotify_client = None

def get_spotify_client():
    global _spotify_client
    if _spotify_client is not None:
        return _spotify_client
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    csecret = os.getenv("SPOTIFY_CLIENT_SECRET")
    if cid and csecret and cid != "YOUR_SPOTIFY_CLIENT_ID" and csecret != "YOUR_SPOTIFY_CLIENT_SECRET":
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            try:
                from spotipy.cache_handler import MemoryCacheHandler
                cache_handler = MemoryCacheHandler()
            except Exception:
                cache_handler = None

            if cache_handler:
                auth_mgr = SpotifyClientCredentials(
                    client_id=cid.strip(),
                    client_secret=csecret.strip(),
                    cache_handler=cache_handler
                )
            else:
                auth_mgr = SpotifyClientCredentials(
                    client_id=cid.strip(),
                    client_secret=csecret.strip()
                )
            _spotify_client = spotipy.Spotify(auth_manager=auth_mgr)
            print("[Spotify API] 成功初始化官方 Spotify API 客戶端！")
        except ImportError:
            pass
        except Exception as e:
            print(f"[Spotify API] 初始化官方客戶端失敗: {e}")
    return _spotify_client

def parse_spotify_html(html: str, item_type: str, item_id: str) -> Optional[dict]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            raw_data = json.loads(match.group(1))
            page_props = raw_data.get("props", {}).get("pageProps", {})
            entity = page_props.get("state", {}).get("data", {}).get("entity") or page_props.get("entity", {})

            if not entity:
                def search_entity(d):
                    if isinstance(d, dict):
                        if "trackList" in d or ("name" in d and ("artists" in d or "subtitle" in d)):
                            return d
                        for k, v in d.items():
                            res = search_entity(v)
                            if res:
                                return res
                    elif isinstance(d, list):
                        for item in d:
                            res = search_entity(item)
                            if res:
                                return res
                    return None
                entity = search_entity(raw_data) or {}

            if entity:
                if item_type == "track":
                    title = entity.get("name") or entity.get("title") or ""
                    artists = entity.get("artists", [])
                    if isinstance(artists, list) and artists:
                        artist_names = [a.get("name") for a in artists if isinstance(a, dict) and a.get("name")]
                        artist = ", ".join(artist_names)
                    else:
                        artist = entity.get("subtitle") or ""

                    duration_ms = entity.get("duration") or 0
                    duration = int(duration_ms) // 1000 if duration_ms else 0

                    cover_sources = entity.get("coverArt", {}).get("sources", [])
                    thumb = cover_sources[0].get("url") if cover_sources else ""

                    if title:
                        return {
                            "type": "track",
                            "title": title,
                            "artist": artist,
                            "thumbnail": thumb,
                            "duration": duration,
                            "tracks": [{
                                "title": title,
                                "artist": artist,
                                "duration": duration,
                                "thumbnail": thumb,
                                "url": f"https://open.spotify.com/track/{item_id}",
                                "search_query": f"{title} {artist}".strip()
                            }]
                        }
                elif item_type in ("playlist", "album"):
                    title = entity.get("name") or entity.get("title") or ("Spotify 播放清單" if item_type == "playlist" else "Spotify 專輯")
                    cover_sources = entity.get("coverArt", {}).get("sources", [])
                    thumb = cover_sources[0].get("url") if cover_sources else ""

                    track_list = entity.get("trackList") or []
                    if not track_list:
                        track_list = entity.get("tracks", {}).get("items", [])

                    tracks = []
                    for item in track_list:
                        t_title = item.get("title") or item.get("name")
                        if not t_title:
                            continue
                        t_artist = item.get("subtitle") or ""
                        if not t_artist and isinstance(item.get("artists"), list):
                            t_artist = ", ".join(a.get("name") for a in item.get("artists") if isinstance(a, dict) and a.get("name"))
                        t_dur_ms = item.get("duration") or 0
                        t_dur = int(t_dur_ms) // 1000 if t_dur_ms else 0
                        uri = item.get("uri") or ""
                        tid = uri.split(":")[-1] if uri else ""
                        t_url = f"https://open.spotify.com/track/{tid}" if tid else ""
                        tracks.append({
                            "title": t_title,
                            "artist": t_artist,
                            "duration": t_dur,
                            "thumbnail": thumb,
                            "url": t_url,
                            "search_query": f"{t_title} {t_artist}".strip()
                        })

                    if tracks:
                        return {
                            "type": item_type,
                            "title": title,
                            "artist": "",
                            "thumbnail": thumb,
                            "duration": sum(tr["duration"] for tr in tracks),
                            "tracks": tracks
                        }
        except Exception as e:
            print(f"[Spotify Parse] JSON 解析錯誤: {e}")

    og_title_m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html)
    og_desc_m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html)
    og_img_m = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html)

    if og_title_m:
        title = og_title_m.group(1).strip()
        artist = ""
        if og_desc_m:
            desc = og_desc_m.group(1).strip()
            parts = desc.split("·")
            if parts:
                artist = parts[0].strip()
        thumb = og_img_m.group(1).strip() if og_img_m else ""
        return {
            "type": item_type,
            "title": title,
            "artist": artist,
            "thumbnail": thumb,
            "duration": 0,
            "tracks": [{
                "title": title,
                "artist": artist,
                "duration": 0,
                "thumbnail": thumb,
                "url": f"https://open.spotify.com/track/{item_id}",
                "search_query": f"{title} {artist}".strip()
            }]
        }

    return None

def fetch_spotify_html_sync(url: str) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
        return response.read().decode('utf-8', errors='ignore')

def fetch_spotify_oembed_sync(spotify_url: str) -> Optional[dict]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    api_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(spotify_url)}"
    req = urllib.request.Request(api_url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
        return json.loads(response.read().decode('utf-8', errors='ignore'))

def extract_spotify_ytdl_sync(spotify_url: str) -> Optional[dict]:
    try:
        ydl = yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True, 'ignoreerrors': True})
        return ydl.extract_info(spotify_url, download=False)
    except Exception:
        return None

def resolve_spotify_sync(url: str) -> Optional[dict]:
    m = SPOTIFY_URL_REGEX.search(url)
    if not m:
        return None
    item_type = m.group(1)
    item_id = m.group(2)

    sp = get_spotify_client()
    if sp:
        try:
            if item_type == 'track':
                track_obj = sp.track(item_id)
                artists = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name'))
                duration = track_obj.get('duration_ms', 0) // 1000
                thumb = ''
                if track_obj.get('album', {}).get('images'):
                    thumb = track_obj['album']['images'][0].get('url', '')
                title = track_obj.get('name', '未知標題')
                spotify_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{item_id}")
                return {
                    'type': 'track',
                    'title': title,
                    'artist': artists,
                    'thumbnail': thumb,
                    'duration': duration,
                    'tracks': [{
                        'title': title,
                        'artist': artists,
                        'duration': duration,
                        'thumbnail': thumb,
                        'url': spotify_url,
                        'search_query': f"{title} {artists}".strip()
                    }]
                }
            elif item_type == 'playlist':
                pl = sp.playlist(item_id)
                pl_title = pl.get('name', 'Spotify 播放清單')
                pl_thumb = pl['images'][0].get('url', '') if pl.get('images') else ''
                tracks = []
                items = pl.get('tracks', {}).get('items', [])
                for item in items[:200]:
                    track_obj = item.get('track')
                    if not track_obj or not track_obj.get('name'):
                        continue
                    t_title = track_obj.get('name')
                    t_artist = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name'))
                    t_dur = track_obj.get('duration_ms', 0) // 1000
                    t_thumb = ''
                    if track_obj.get('album', {}).get('images'):
                        t_thumb = track_obj['album']['images'][0].get('url', '')
                    t_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{track_obj.get('id', '')}")
                    tracks.append({
                        'title': t_title,
                        'artist': t_artist,
                        'duration': t_dur,
                        'thumbnail': t_thumb or pl_thumb,
                        'url': t_url,
                        'search_query': f"{t_title} {t_artist}".strip()
                    })
                return {
                    'type': 'playlist',
                    'title': pl_title,
                    'artist': '',
                    'thumbnail': pl_thumb,
                    'duration': sum(tr['duration'] for tr in tracks),
                    'tracks': tracks
                }
            elif item_type == 'album':
                album = sp.album(item_id)
                album_title = album.get('name', 'Spotify 專輯')
                album_thumb = album['images'][0].get('url', '') if album.get('images') else ''
                album_artist = ", ".join(a['name'] for a in album.get('artists', []) if a.get('name'))
                tracks = []
                items = album.get('tracks', {}).get('items', [])
                for track_obj in items[:200]:
                    if not track_obj or not track_obj.get('name'):
                        continue
                    t_title = track_obj.get('name')
                    t_artist = ", ".join(a['name'] for a in track_obj.get('artists', []) if a.get('name')) or album_artist
                    t_dur = track_obj.get('duration_ms', 0) // 1000
                    t_url = track_obj.get('external_urls', {}).get('spotify', f"https://open.spotify.com/track/{track_obj.get('id', '')}")
                    tracks.append({
                        'title': t_title,
                        'artist': t_artist,
                        'duration': t_dur,
                        'thumbnail': album_thumb,
                        'url': t_url,
                        'search_query': f"{t_title} {t_artist}".strip()
                    })
                return {
                    'type': 'album',
                    'title': album_title,
                    'artist': album_artist,
                    'thumbnail': album_thumb,
                    'duration': sum(tr['duration'] for tr in tracks),
                    'tracks': tracks
                }
        except Exception as e:
            print(f"[Spotify API] 官方 API 解析失敗: {e}，自動切換至爬蟲模式...")

    embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
    try:
        html = fetch_spotify_html_sync(embed_url)
        parsed = parse_spotify_html(html, item_type, item_id)
        if parsed and parsed.get('tracks'):
            return parsed
    except Exception as e:
        print(f"[Spotify Embed] Embed 爬蟲解析失敗: {e}")

    if item_type == 'track':
        try:
            oembed_data = fetch_spotify_oembed_sync(f"https://open.spotify.com/track/{item_id}")
            if oembed_data and oembed_data.get('title'):
                title = oembed_data.get('title', '')
                thumb = oembed_data.get('thumbnail_url', '')
                author = oembed_data.get('author_name', '')
                return {
                    'type': 'track',
                    'title': title,
                    'artist': author,
                    'thumbnail': thumb,
                    'duration': 0,
                    'tracks': [{
                        'title': title,
                        'artist': author,
                        'duration': 0,
                        'thumbnail': thumb,
                        'url': f"https://open.spotify.com/track/{item_id}",
                        'search_query': f"{title} {author}".strip()
                    }]
                }
        except Exception as e:
            print(f"[Spotify oEmbed] oEmbed 解析失敗: {e}")

    try:
        ytdl_res = extract_spotify_ytdl_sync(url)
        if ytdl_res:
            title = ytdl_res.get('title', '')
            artist = ytdl_res.get('artist') or ytdl_res.get('uploader') or ''
            thumb = ytdl_res.get('thumbnail', '')
            dur = ytdl_res.get('duration', 0)
            entries = ytdl_res.get('entries', [])
            if entries:
                tracks = []
                for e in entries:
                    if not e:
                        continue
                    etitle = e.get('title', '')
                    eartist = e.get('artist') or e.get('uploader') or ''
                    tracks.append({
                        'title': etitle,
                        'artist': eartist,
                        'duration': e.get('duration', 0),
                        'thumbnail': e.get('thumbnail') or thumb,
                        'url': e.get('webpage_url') or e.get('url') or '',
                        'search_query': f"{etitle} {eartist}".strip()
                    })
                if tracks:
                    return {
                        'type': item_type,
                        'title': title or 'Spotify 播放清單',
                        'artist': artist,
                        'thumbnail': thumb,
                        'duration': sum(tr['duration'] for tr in tracks),
                        'tracks': tracks
                    }
            elif title:
                return {
                    'type': 'track',
                    'title': title,
                    'artist': artist,
                    'thumbnail': thumb,
                    'duration': dur,
                    'tracks': [{
                        'title': title,
                        'artist': artist,
                        'duration': dur,
                        'thumbnail': thumb,
                        'url': url,
                        'search_query': f"{title} {artist}".strip()
                    }]
                }
    except Exception as e:
        print(f"[Spotify yt-dlp] yt-dlp flat 解析失敗: {e}")

    return None

async def resolve_spotify(url: str) -> Optional[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: resolve_spotify_sync(url))

def resolve_netease_sync(url: str) -> Optional[dict]:
    if "163cn.tv" in url or "163.lu" in url or "/s/" in url:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                url = resp.geturl()
        except Exception:
            pass
    m = NETEASE_URL_REGEX.search(url)
    if not m:
        return None
    item_type = m.group(1).lower()
    item_id = m.group(2)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://music.163.com/'
    }
    ctx = ssl._create_unverified_context()
    if item_type == 'song':
        try:
            api_url = f"https://music.163.com/api/song/detail/?id={item_id}&ids=[{item_id}]"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            songs = data.get('songs', [])
            if not songs:
                return None
            s = songs[0]
            title = s.get('name', '未知歌曲')
            artists = ", ".join(a.get('name', '') for a in s.get('artists', []) if a.get('name'))
            duration = (s.get('duration', 0) or 0) // 1000
            thumb = s.get('album', {}).get('picUrl', '')
            song_url = f"https://music.163.com/#/song?id={item_id}"
            return {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'track',
                'title': title,
                'artist': artists,
                'thumbnail': thumb,
                'duration': duration,
                'tracks': [{
                    'title': title,
                    'artist': artists,
                    'duration': duration,
                    'thumbnail': thumb,
                    'url': song_url,
                    'search_query': f"{title} {artists}".strip()
                }]
            }
        except Exception as e:
            print(f"[Netease] 單曲解析異常: {e}")
            return None
    elif item_type == 'playlist':
        try:
            api_url = f"https://music.163.com/api/v1/playlist/detail?id={item_id}"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            pl = data.get('playlist', {})
            pl_name = pl.get('name', '網易雲歌單')
            pl_thumb = pl.get('coverImgUrl', '')
            raw_tracks = pl.get('tracks', [])
            tracks = []
            for t_item in raw_tracks[:200]:
                title = t_item.get('name', '未知歌曲')
                artist_list = t_item.get('ar') or t_item.get('artists') or []
                artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
                duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
                thumb = t_item.get('al', {}).get('picUrl') or pl_thumb
                tid = t_item.get('id')
                song_url = f"https://music.163.com/#/song?id={tid}" if tid else url
                tracks.append({
                    'title': title,
                    'artist': artists,
                    'duration': duration,
                    'thumbnail': thumb,
                    'url': song_url,
                    'search_query': f"{title} {artists}".strip()
                })
            return {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'playlist',
                'title': pl_name,
                'artist': pl.get('creator', {}).get('nickname', ''),
                'thumbnail': pl_thumb,
                'duration': sum(t['duration'] for t in tracks),
                'tracks': tracks
            }
        except Exception as e:
            print(f"[Netease] 歌單解析異常: {e}")
            return None
    elif item_type == 'album':
        try:
            api_url = f"https://music.163.com/api/v1/album/{item_id}"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            album = data.get('album', {})
            album_name = album.get('name', '網易雲專輯')
            album_thumb = album.get('picUrl', '')
            raw_songs = data.get('songs', [])
            tracks = []
            for t_item in raw_songs[:200]:
                title = t_item.get('name', '未知歌曲')
                artist_list = t_item.get('ar') or t_item.get('artists') or []
                artists = ", ".join(a.get('name', '') for a in artist_list if a.get('name'))
                duration = (t_item.get('dt', 0) or t_item.get('duration', 0) or 0) // 1000
                thumb = album_thumb
                tid = t_item.get('id')
                song_url = f"https://music.163.com/#/song?id={tid}" if tid else url
                tracks.append({
                    'title': title,
                    'artist': artists,
                    'duration': duration,
                    'thumbnail': thumb,
                    'url': song_url,
                    'search_query': f"{title} {artists}".strip()
                })
            return {
                'platform': 'netease',
                'platform_name': '網易雲音樂',
                'color': 0xC20C0C,
                'type': 'album',
                'title': album_name,
                'artist': album.get('artist', {}).get('name', ''),
                'thumbnail': album_thumb,
                'duration': sum(t['duration'] for t in tracks),
                'tracks': tracks
            }
        except Exception as e:
            print(f"[Netease] 專輯解析異常: {e}")
            return None
    return None

async def resolve_netease(url: str) -> Optional[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: resolve_netease_sync(url))

def format_apple_artwork(art_obj) -> str:
    if not art_obj:
        return ''
    raw_url = art_obj.get('dictionary', {}).get('url') if isinstance(art_obj, dict) else ''
    if not raw_url and isinstance(art_obj, dict):
        raw_url = art_obj.get('url', '')
    if raw_url:
        return raw_url.replace('{w}', '600').replace('{h}', '600').replace('{f}', 'jpg')
    return ''

def resolve_apple_music_sync(url: str) -> Optional[dict]:
    if "apple.co" in url:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                url = resp.geturl()
        except Exception:
            pass
    m = APPLE_MUSIC_URL_REGEX.search(url)
    if not m:
        return None
    country = m.group(1) or 'us'
    item_type = m.group(2).lower()
    slug_or_id = m.group(3)
    id_part = m.group(4)
    track_id_param = m.group(5)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    ctx = ssl._create_unverified_context()
    if track_id_param:
        try:
            api_url = f"https://itunes.apple.com/lookup?id={track_id_param}&country={country}"
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            results = data.get('results', [])
            if results:
                r = results[0]
                title = r.get('trackName', '未知歌曲')
                artist = r.get('artistName', '')
                duration = (r.get('trackTimeMillis', 0) or 0) // 1000
                thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                return {
                    'platform': 'apple_music',
                    'platform_name': 'Apple Music',
                    'color': 0xFC3C44,
                    'type': 'track',
                    'title': title,
                    'artist': artist,
                    'thumbnail': thumb,
                    'duration': duration,
                    'tracks': [{
                        'title': title,
                        'artist': artist,
                        'duration': duration,
                        'thumbnail': thumb,
                        'url': r.get('trackViewUrl', url),
                        'search_query': f"{title} {artist}".strip()
                    }]
                }
        except Exception as e:
            print(f"[Apple Music] 單曲參數解析異常: {e}")

    if item_type == 'song':
        song_id = id_part if (id_part and id_part.isdigit()) else (slug_or_id if slug_or_id.isdigit() else None)
        if song_id:
            try:
                api_url = f"https://itunes.apple.com/lookup?id={song_id}&country={country}"
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                results = data.get('results', [])
                if results:
                    r = results[0]
                    title = r.get('trackName', '未知歌曲')
                    artist = r.get('artistName', '')
                    duration = (r.get('trackTimeMillis', 0) or 0) // 1000
                    thumb = r.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    return {
                        'platform': 'apple_music',
                        'platform_name': 'Apple Music',
                        'color': 0xFC3C44,
                        'type': 'track',
                        'title': title,
                        'artist': artist,
                        'thumbnail': thumb,
                        'duration': duration,
                        'tracks': [{
                            'title': title,
                            'artist': artist,
                            'duration': duration,
                            'thumbnail': thumb,
                            'url': r.get('trackViewUrl', url),
                            'search_query': f"{title} {artist}".strip()
                        }]
                    }
            except Exception as e:
                print(f"[Apple Music] 單曲解析異常: {e}")

    if item_type == 'album':
        album_id = id_part if (id_part and id_part.isdigit()) else (slug_or_id if slug_or_id.isdigit() else None)
        if album_id:
            try:
                api_url = f"https://itunes.apple.com/lookup?id={album_id}&entity=song&country={country}"
                req = urllib.request.Request(api_url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                results = data.get('results', [])
                if results:
                    col = results[0]
                    col_name = col.get('collectionName', 'Apple Music 專輯')
                    col_artist = col.get('artistName', '')
                    col_thumb = col.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
                    song_results = [r for r in results if r.get('wrapperType') == 'track']
                    tracks = []
                    for s in song_results[:200]:
                        t_title = s.get('trackName', '未知歌曲')
                        t_artist = s.get('artistName', col_artist)
                        t_dur = (s.get('trackTimeMillis', 0) or 0) // 1000
                        t_thumb = s.get('artworkUrl100', '').replace('100x100bb', '600x600bb') or col_thumb
                        tracks.append({
                            'title': t_title,
                            'artist': t_artist,
                            'duration': t_dur,
                            'thumbnail': t_thumb,
                            'url': s.get('trackViewUrl', url),
                            'search_query': f"{t_title} {t_artist}".strip()
                        })
                    return {
                        'platform': 'apple_music',
                        'platform_name': 'Apple Music',
                        'color': 0xFC3C44,
                        'type': 'album',
                        'title': col_name,
                        'artist': col_artist,
                        'thumbnail': col_thumb,
                        'duration': sum(t['duration'] for t in tracks),
                        'tracks': tracks
                    }
            except Exception as e:
                print(f"[Apple Music] 專輯解析異常: {e}")

    if item_type == 'playlist':
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                html = resp.read().decode('utf-8')
            m_json = re.search(r'<script type=\"application/json\" id=\"serialized-server-data\">(.*?)</script>', html, re.DOTALL)
            if m_json:
                parsed = json.loads(m_json.group(1))
                pdata = parsed if isinstance(parsed, dict) else (parsed[0] if parsed else {})
                data_list = pdata.get('data', [])
                if data_list:
                    inner_data = data_list[0].get('data', {})
                    sections = inner_data.get('sections', [])
                    pl_name = 'Apple Music 歌單'
                    pl_thumb = ''
                    if sections and sections[0].get('items'):
                        h_item = sections[0]['items'][0]
                        pl_name = h_item.get('title') or pl_name
                        pl_thumb = format_apple_artwork(h_item.get('artwork'))
                    if not pl_name or pl_name == 'Apple Music 歌單':
                        seo_title = inner_data.get('seoData', {}).get('pageTitle', '')
                        if seo_title:
                            pl_name = seo_title.split(' - ')[0]

                    best_section = max(sections, key=lambda s: len(s.get('items', []))) if sections else {}
                    items = best_section.get('items', [])
                    tracks = []
                    for it in items[:200]:
                        t_title = it.get('title')
                        if not t_title:
                            continue
                        t_artist = it.get('artistName', '')
                        t_dur = (it.get('duration', 0) or 0) // 1000
                        t_thumb = format_apple_artwork(it.get('artwork')) or pl_thumb
                        t_url = it.get('contentDescriptor', {}).get('url') or url
                        tracks.append({
                            'title': t_title,
                            'artist': t_artist,
                            'duration': t_dur,
                            'thumbnail': t_thumb,
                            'url': t_url,
                            'search_query': f"{t_title} {t_artist}".strip()
                        })
                    if tracks:
                        return {
                            'platform': 'apple_music',
                            'platform_name': 'Apple Music',
                            'color': 0xFC3C44,
                            'type': 'playlist',
                            'title': pl_name,
                            'artist': 'Apple Music',
                            'thumbnail': pl_thumb,
                            'duration': sum(t['duration'] for t in tracks),
                            'tracks': tracks
                        }
        except Exception as e:
            print(f"[Apple Music] 歌單解析異常: {e}")

    return None

async def resolve_apple_music(url: str) -> Optional[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: resolve_apple_music_sync(url))

def is_vc_connected(vc) -> bool:
    if not vc:
        return False
    if hasattr(vc, 'connected'):
        return bool(vc.connected)
    if hasattr(vc, 'is_connected') and callable(vc.is_connected):
        return bool(vc.is_connected())
    return hasattr(vc, 'channel') and vc.channel is not None

def is_vc_playing(vc) -> bool:
    if not vc:
        return False
    if hasattr(vc, 'playing'):
        return bool(vc.playing)
    if hasattr(vc, 'is_playing') and callable(vc.is_playing):
        return bool(vc.is_playing())
    return False

def is_vc_paused(vc) -> bool:
    if not vc:
        return False
    if hasattr(vc, 'paused'):
        return bool(vc.paused)
    if hasattr(vc, 'is_paused') and callable(vc.is_paused):
        return bool(vc.is_paused())
    return False

def is_vc_active(vc) -> bool:
    return is_vc_playing(vc) or is_vc_paused(vc)

async def safe_pause_resume(vc, state: Optional['GuildMusicState'] = None) -> str:
    if not vc:
        return "none"
    if hasattr(vc, 'paused'):
        if vc.paused:
            if asyncio.iscoroutinefunction(vc.pause):
                await vc.pause(False)
            else:
                vc.pause(False)
            if state and state.pause_timestamp > 0:
                state.paused_duration += time.time() - state.pause_timestamp
                state.pause_timestamp = 0.0
            return "resumed"
        else:
            if asyncio.iscoroutinefunction(vc.pause):
                await vc.pause(True)
            else:
                vc.pause(True)
            if state:
                state.pause_timestamp = time.time()
            return "paused"
    if is_vc_paused(vc):
        if hasattr(vc, 'resume') and callable(vc.resume):
            vc.resume()
            if state and state.pause_timestamp > 0:
                state.paused_duration += time.time() - state.pause_timestamp
                state.pause_timestamp = 0.0
            return "resumed"
    elif is_vc_playing(vc):
        if hasattr(vc, 'pause') and callable(vc.pause):
            vc.pause()
            if state:
                state.pause_timestamp = time.time()
            return "paused"
    return "none"

async def safe_stop(vc):
    if not vc:
        return
    try:
        if hasattr(vc, 'stop'):
            if asyncio.iscoroutinefunction(vc.stop):
                await vc.stop()
            else:
                vc.stop()
    except Exception as e:
        print(f"[Safe Stop Error] {e}")

async def safe_disconnect(vc):
    if not vc:
        return
    try:
        if hasattr(vc, 'disconnect'):
            if asyncio.iscoroutinefunction(vc.disconnect):
                try:
                    await vc.disconnect(force=True)
                except TypeError:
                    await vc.disconnect()
            else:
                try:
                    vc.disconnect(force=True)
                except TypeError:
                    vc.disconnect()
    except Exception as e:
        print(f"[Safe Disconnect Error] {e}")

def get_playback_elapsed(state: GuildMusicState) -> int:
    if not state.current or state.start_time == 0:
        return 0
    now = time.time()
    if state.pause_timestamp > 0:
        elapsed = state.pause_timestamp - state.start_time - state.paused_duration
    else:
        elapsed = now - state.start_time - state.paused_duration
    elapsed = max(0, int(elapsed))
    if state.current.duration and state.current.duration > 0:
        elapsed = min(elapsed, state.current.duration)
    return elapsed

def make_progress_bar(elapsed: int, total: int, length: int = 14) -> str:
    if not total or total <= 0:
        return "🔴 `LIVE 直播音訊`"
    percent = min(1.0, max(0.0, elapsed / total))
    pos = int(percent * length)
    pos = min(pos, length - 1)
    bar = "━" * pos + "🔘" + "─" * (length - 1 - pos)
    return f"`{format_duration(elapsed)}` {bar} `{format_duration(total)}`"

def get_platform_badge(url: str, uploader: str) -> Tuple[str, int]:
    u = (url or "").lower()
    up = (uploader or "").lower()
    if "spotify" in u or "spotify" in up:
        return "Spotify", 0x1DB954
    elif "163.com" in u or "163cn" in u or "163.lu" in u or "netease" in up or "網易" in up:
        return "網易雲音樂", 0xC20C0C
    elif "apple.com" in u or "apple.co" in u or "apple" in up:
        return "Apple Music", 0xFC3C44
    elif "soundcloud" in u:
        return "SoundCloud", 0xFF5500
    return "YouTube Music", 0xFF0000

class MusicControllerView(discord.ui.View):
    def __init__(self, bot: 'MusicNodeBot', state: GuildMusicState):
        super().__init__(timeout=None)
        self.bot = bot
        self.state = state
        self._update_button_states()

    def _update_button_states(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "music_loop":
                    if self.state.loop_mode == 'single':
                        child.emoji = "🔂"
                        child.label = "單曲"
                        child.style = discord.ButtonStyle.success
                    elif self.state.loop_mode == 'queue':
                        child.emoji = "🔁"
                        child.label = "歌單"
                        child.style = discord.ButtonStyle.primary
                    else:
                        child.emoji = "🔁"
                        child.label = "循環"
                        child.style = discord.ButtonStyle.secondary
                elif child.custom_id == "music_autoplay":
                    child.style = discord.ButtonStyle.success if self.state.autoplay else discord.ButtonStyle.secondary

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0, custom_id="music_replay")
    async def replay(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or not self.state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.queue.appendleft(self.state.current)
        await safe_stop(vc)
        await interaction.response.send_message("⏮️ 重播當前歌曲！", ephemeral=True)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, row=0, custom_id="music_play_pause")
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return
        res = await safe_pause_resume(vc, self.state)
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0, custom_id="music_skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        if self.state.loop_mode == 'single':
            self.state.loop_mode = 'off'
        await safe_stop(vc)
        await interaction.response.send_message(t('skipped', interaction, bot_name=self.bot.node_name))

    @discord.ui.button(emoji="🔁", label="循環", style=discord.ButtonStyle.secondary, row=0, custom_id="music_loop")
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        modes = ['off', 'single', 'queue']
        idx = modes.index(self.state.loop_mode)
        self.state.loop_mode = modes[(idx + 1) % len(modes)]
        self._update_button_states()
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0, custom_id="music_stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        self.state.queue.clear()
        self.state.current = None
        self.state.loop_mode = 'off'
        await safe_stop(vc)
        await safe_disconnect(vc)
        await interaction.response.send_message(t('stopped_and_left', interaction, bot_name=self.bot.node_name))

    @discord.ui.button(label="-10%", emoji="🔉", style=discord.ButtonStyle.secondary, row=1, custom_id="music_vol_down")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.volume = max(0.0, round(self.state.volume - 0.1, 2))
        if hasattr(vc, 'source') and hasattr(vc.source, 'volume'):
            vc.source.volume = self.state.volume
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="+10%", emoji="🔊", style=discord.ButtonStyle.secondary, row=1, custom_id="music_vol_up")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.volume = min(2.0, round(self.state.volume + 0.1, 2))
        if hasattr(vc, 'source') and hasattr(vc.source, 'volume'):
            vc.source.volume = self.state.volume
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="接歌", emoji="📻", style=discord.ButtonStyle.secondary, row=1, custom_id="music_autoplay")
    async def toggle_autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.autoplay = not self.state.autoplay
        self._update_button_states()
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="歌詞", emoji="💬", style=discord.ButtonStyle.secondary, row=1, custom_id="music_lyrics")
    async def view_lyrics(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        target_title = self.state.current.title
        target_artist = self.state.current.uploader or ""
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: get_lyrics(target_title, target_artist))
        if not res:
            await interaction.followup.send(t('lyrics_not_found', interaction, title=target_title), ephemeral=True)
            return
        display_title, lrc_text, is_synced = res
        if len(lrc_text) > 4000:
            lrc_text = lrc_text[:3990] + "\n..."
        embed = discord.Embed(
            title=f"💬 【{display_title}】即時歌詞",
            description=lrc_text,
            color=0x1DB954 if is_synced else 0x5865F2
        )
        sync_str = "⚡ 動態時間軸 (Synced)" if is_synced else "📝 純文字 (Plain)"
        embed.set_footer(text=f"模式: {sync_str} • 資料庫: LRCLIB / Netease")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="隊列", emoji="📜", style=discord.ButtonStyle.secondary, row=1, custom_id="music_queue")
    async def view_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_queue_embed(self.bot, self.state, lang=resolve_locale(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

def build_nowplaying_embed(bot: 'MusicNodeBot', song: Song, state: GuildMusicState, lang: Optional[str] = None) -> discord.Embed:
    guild_lang = lang or resolve_locale(guild_id=state.guild_id)
    loop_str = t(f'loop_{state.loop_mode}', lang=guild_lang)

    guild = bot.get_guild(state.guild_id)
    vc = guild.voice_client if guild else None
    bitrate_str = "128 kbps"
    if vc and hasattr(vc, 'channel') and vc.channel and hasattr(vc.channel, 'bitrate') and vc.channel.bitrate:
        bitrate_str = f"{vc.channel.bitrate // 1000} kbps"

    bb_label = BASSBOOST_PRESETS.get(state.bassboost, {}).get("label", "關閉")
    eq_label = EQ_PRESETS.get(state.filter, {}).get("label", "原音直通")
    platform_name, embed_color = get_platform_badge(song.url, song.uploader)

    elapsed = get_playback_elapsed(state)
    dur = song.duration or 0
    progress_line = make_progress_bar(elapsed, dur, length=15)

    embed = discord.Embed(
        title=f"💿 正在播放 • {platform_name}",
        description=f"### [{song.title}]({song.url})\n👤 演出者: **{song.uploader or '未知演出者'}**\n\n{progress_line}",
        color=embed_color
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    embed.add_field(name="👤 點播者", value=song.requester.mention, inline=True)
    embed.add_field(name="🔊 音量", value=f"`{int(state.volume * 100)}%`", inline=True)
    embed.add_field(name="🔁 循環模式", value=f"`{loop_str}`", inline=True)
    embed.add_field(name="🎛️ 音訊引擎", value=f"`{state.active_engine}`", inline=True)
    embed.add_field(name="📻 自動接歌", value=f"`{'開啟' if state.autoplay else '關閉'}`", inline=True)
    embed.add_field(name="📜 待播隊列", value=f"`{len(state.queue)} 首歌曲`", inline=True)

    bot_name = bot.node_name
    embed.set_footer(
        text=f"DJ Donut • {bot_name} • 48kHz / {bitrate_str} • EQ: {eq_label} • 重低音: {bb_label}"
    )
    return embed

def build_queue_embed(bot: 'MusicNodeBot', state: GuildMusicState, lang: Optional[str] = None) -> discord.Embed:
    guild_lang = lang or resolve_locale(guild_id=state.guild_id)
    embed = discord.Embed(
        title=f"📜 【{bot.node_name}】語音房待播清單",
        color=0x5865F2
    )
    if state.current:
        elapsed = get_playback_elapsed(state)
        dur = state.current.duration
        time_tag = f"`[{format_duration(elapsed)} / {format_duration(dur)}]`" if dur else "`[LIVE 直播]`"
        embed.description = f"### ▶️ 現正播放\n**[{state.current.title}]({state.current.url})**\n{time_tag} • 由 {state.current.requester.mention} 點播\n"
    else:
        embed.description = "⏸️ **目前沒有正在播放的歌曲**\n使用 `/play <歌曲或網址>` 即可開始點播！\n"

    if not state.queue:
        embed.add_field(
            name="📋 待播歌曲 (0)",
            value="*目前隊列為空，快使用 `/play` 點歌，或開啟 `/autoplay` 電台自動接歌！*",
            inline=False
        )
    else:
        total_seconds = sum(s.duration for s in state.queue if s.duration)
        total_dur_str = format_duration(total_seconds) if total_seconds else "未知"
        queue_lines = []
        for i, s in enumerate(list(state.queue)[:10], start=1):
            s_dur = f"`{format_duration(s.duration)}`" if s.duration else "`直播`"
            queue_lines.append(f"`{i:02d}.` **[{s.title[:35]}]({s.url})** ({s_dur}) • {s.requester.mention}")

        remaining = len(state.queue) - 10
        if remaining > 0:
            queue_lines.append(f"\n*...以及其他 **{remaining}** 首歌曲*")

        header_meta = f"總計 `{len(state.queue)}` 首歌曲 • 預估總時長 `{total_dur_str}`\n\n"
        embed.add_field(
            name="📋 待播歌曲清單",
            value=header_meta + "\n".join(queue_lines),
            inline=False
        )

    loop_str = t(f'loop_{state.loop_mode}', lang=guild_lang)
    embed.set_footer(
        text=f"🔁 循環: {loop_str} • 📻 自動接歌: {'開啟' if state.autoplay else '關閉'} • 🔊 音量: {int(state.volume * 100)}%"
    )
    return embed

def build_song_queued_embed(bot: 'MusicNodeBot', song: Song, state: GuildMusicState, pos: int, interaction: discord.Interaction) -> discord.Embed:
    platform_name, embed_color = get_platform_badge(song.url, song.uploader)
    dur_str = format_duration(song.duration, resolve_locale(interaction))
    embed = discord.Embed(
        title=f"✅ 已加入隊列 • {platform_name}",
        description=f"### [{song.title}]({song.url})\n👤 演出者: **{song.uploader or '未知'}**",
        color=embed_color
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    embed.add_field(name="⏱️ 歌曲時長", value=f"`{dur_str}`", inline=True)
    embed.add_field(name="📍 播放順位", value=f"`#{pos}`", inline=True)
    embed.add_field(name="🤖 服務節點", value=f"`{bot.node_name}`", inline=True)
    embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
    embed.add_field(name="📊 隊列總計", value=f"`{len(state.queue)} 首待播`", inline=True)
    embed.add_field(name="🔊 音量設定", value=f"`{int(state.volume * 100)}%`", inline=True)
    embed.set_footer(text="DJ Donut • 輸入 /nowplaying 查看控制器 • /queue 查看待播清單")
    return embed

async def check_lavalink_health(uri: str, password: str) -> bool:
    if not uri:
        return False
    base_uri = uri.rstrip('/')
    test_url = f"{base_uri}/v4/info"
    loop = asyncio.get_event_loop()

    def _probe_sync():
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            headers = {
                'Authorization': password,
                'User-Agent': 'Wavelink/3.0 (DiscordBot)'
            }
            req = urllib.request.Request(test_url, headers=headers, method='GET')
            with urllib.request.urlopen(req, context=ctx, timeout=2.5) as resp:
                if resp.status == 200:
                    ct = resp.headers.get('Content-Type', '')
                    if 'application/json' in ct:
                        body = resp.read()
                        data = json.loads(body)
                        return bool(data.get('version') or data.get('jvm'))
            return False
        except Exception:
            return False

    return await loop.run_in_executor(None, _probe_sync)

NODE_BOTS: List['MusicNodeBot'] = []

class MusicNodeBot(commands.Bot):
    def __init__(self, node_id: int):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True

        self.node_id = node_id
        self.node_name = f"音樂 {node_id} 號"
        self.states: Dict[int, GuildMusicState] = {}
        self.lavalink_connected = False

        super().__init__(
            command_prefix=[f"{node_id}!", f"!{node_id}", "!"],
            intents=intents,
            help_command=None
        )

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildMusicState(guild_id)
        return self.states[guild_id]

    async def setup_hook(self):
        if HAS_WAVELINK and LAVALINK_URI:
            if len(TOKENS) > 1:
                self.lavalink_connected = False
                if self.node_id == 1:
                    print(f"[{self.node_name}] 🎛️ 檢測到啟用多機矩陣 ({len(TOKENS)} 隻機器人)，為確保各語音房 100% 獨立純淨音訊與發送無衝突，全面啟用極限調校原生獨立引擎！")
            else:
                is_healthy = await check_lavalink_health(LAVALINK_URI, LAVALINK_PASSWORD)
                if is_healthy:
                    try:
                        node = wavelink.Node(
                            identifier=f"Node_{self.node_id}",
                            uri=LAVALINK_URI,
                            password=LAVALINK_PASSWORD
                        )
                        await wavelink.Pool.connect(nodes=[node], client=self)
                        self.lavalink_connected = True
                        print(f"[{self.node_name}] 🚀 成功連線至商業級 Lavalink v4 音訊節點 ({LAVALINK_URI})！")
                    except Exception as e:
                        self.lavalink_connected = False
                        print(f"[{self.node_name}] ⚠️ Lavalink 連線失敗，自動無縫啟用極限調校原生引擎: {e}")
                else:
                    self.lavalink_connected = False
                    print(f"[{self.node_name}] ⚠️ Lavalink 節點 ({LAVALINK_URI}) 響應異常或返回非 JSON (404/403)，自動切換至極限原生獨立引擎！")

        if self.node_id == 1:
            print(f"[{self.node_name}] 正在註冊統一調度斜線指令...")
            register_slash_commands(self)
            await self.tree.sync()
            print(f"[{self.node_name}] 斜線指令同步完畢！")

    async def on_wavelink_node_ready(self, node: 'wavelink.Node'):
        self.lavalink_connected = True
        print(f"[{self.node_name}] 🚀 Lavalink v4 節點 ({node.identifier}) 握手成功，Opus 零損耗直通已啟用！")

    async def on_wavelink_node_closed(self, node: 'wavelink.Node', disconnected: bool):
        self.lavalink_connected = False
        print(f"[{self.node_name}] ⚠️ Lavalink 節點 ({node.identifier}) 斷開連線，已切換為原生引擎。")

    async def on_wavelink_track_end(self, payload: 'wavelink.TrackEndEventPayload'):
        player = getattr(payload, 'player', None)
        if not player or not player.guild:
            return
        if getattr(payload, 'reason', '') in ('REPLACED',):
            return
        await play_next(self, player.guild)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild
        if member.id == self.user.id and after.channel is None:
            state = self.get_state(guild.id)
            state.loop_mode = 'off'
            state.current = None
            state.queue.clear()
            state.is_playing = False
            return

    async def on_ready(self):
        engine_str = "⚡ Lavalink v4 (Opus Passthrough)" if self.lavalink_connected else "🔥 極限原生獨立引擎 (384k VBR)"
        print(f"✅ 【{self.node_name}】已成功連線！名稱: {self.user.name} (音訊引擎: {engine_str})")
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name=f"音樂派對 | {self.node_name} 待命中"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)
def _fetch_autoplay_sync(last_title: str) -> Optional[dict]:
    try:
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', last_title).strip()
        clean_title = re.sub(r'(official\s+video|official\s+audio|mv|lyrics|feat\..*|ft\..*)', '', clean_title, flags=re.I).strip()
        query = f"ytsearch5:{clean_title} radio"
        ydl = yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=True, noplaylist=True))
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
        print(f"[Autoplay Helper] {e}")
    return None

async def fetch_autoplay_recommendation(last_played: Song, loop) -> Optional[Song]:
    entry = await loop.run_in_executor(None, lambda: _fetch_autoplay_sync(last_played.title))
    if entry:
        return Song(entry, requester=last_played.requester)
    return None

async def play_next(bot: MusicNodeBot, guild: discord.Guild):
    state = bot.get_state(guild.id)
    vc = guild.voice_client

    if not vc or not is_vc_connected(vc):
        state.is_playing = False
        return

    if state.current:
        state.last_played = state.current
        if state.loop_mode == 'single':
            state.queue.appendleft(state.current)
        elif state.loop_mode == 'queue':
            state.queue.append(state.current)

    if not state.queue:
        if state.autoplay and state.last_played:
            try:
                rec_song = await fetch_autoplay_recommendation(state.last_played, bot.loop)
                if rec_song:
                    state.queue.append(rec_song)
                    if state.text_channel:
                        try:
                            await state.text_channel.send(t('autoplay_next_song', guild_id=guild.id, title=rec_song.title, url=rec_song.url))
                        except Exception:
                            pass
            except Exception as e:
                print(f"[{bot.node_name} Autoplay Error] {e}")

        if not state.queue:
            state.current = None
            state.is_playing = False
            if state.text_channel:
                try:
                    await state.text_channel.send(t('queue_finished', guild_id=guild.id, bot_name=bot.node_name))
                except Exception:
                    pass
            return

    next_song = state.queue.popleft()
    state.current = next_song
    state.is_playing = True
    state.start_time = time.time()
    state.paused_duration = 0.0
    state.pause_timestamp = 0.0

    if bot.lavalink_connected and HAS_WAVELINK and hasattr(vc, 'play') and hasattr(vc, 'filters'):
        if not next_song.wavelink_track:
            try:
                search_query = next_song.title
                tracks = await wavelink.Playable.search(search_query)
                if not tracks and next_song.url and not (SPOTIFY_URL_REGEX.search(next_song.url) or NETEASE_URL_REGEX.search(next_song.url) or APPLE_MUSIC_URL_REGEX.search(next_song.url)):
                    tracks = await wavelink.Playable.search(next_song.url)
                if not tracks:
                    tracks = await wavelink.Playable.search(f"ytsearch:{search_query}")
                if tracks:
                    next_song.wavelink_track = tracks[0]
            except Exception as search_err:
                print(f"[{bot.node_name}] Lavalink 搜尋音軌略過 ({search_err})")

        if next_song.wavelink_track:
            try:
                state.active_engine = "⚡ Lavalink v4 (Opus Passthrough)"
                filters = vc.filters
                bb_gain = BASSBOOST_PRESETS.get(state.bassboost, {}).get("gain", 0.0)
                if bb_gain > 0:
                    filters.equalizer.set(bands=[
                        {"band": 0, "gain": bb_gain},
                        {"band": 1, "gain": bb_gain * 0.8},
                        {"band": 2, "gain": bb_gain * 0.5},
                        {"band": 3, "gain": bb_gain * 0.2}
                    ])
                else:
                    filters.equalizer.reset()

                if state.filter == "nightcore":
                    filters.timescale.set(speed=1.25, pitch=1.25)
                elif state.filter == "vaporwave":
                    filters.timescale.set(speed=0.85, pitch=0.8)
                elif state.filter == "8d":
                    filters.rotation.set(hertz=0.2)
                elif state.filter == "reset" and bb_gain == 0:
                    filters.reset()

                await vc.set_filters(filters)
                await vc.play(next_song.wavelink_track)
                if hasattr(vc, 'set_volume'):
                    await vc.set_volume(int(state.volume * 100))

                if state.text_channel:
                    embed = build_nowplaying_embed(bot, next_song, state)
                    view = MusicControllerView(bot, state)
                    await state.text_channel.send(embed=embed, view=view)
                return
            except Exception as e:
                print(f"[{bot.node_name}] Lavalink 播放異常 ({e})，自動切換至極限原生獨立引擎...")

    if hasattr(vc, 'filters'):
        channel = getattr(vc, 'channel', None)
        await safe_disconnect(vc)
        if channel:
            try:
                vc = await channel.connect(reconnect=True, timeout=30.0, self_deaf=True)
            except Exception as conn_err:
                print(f"[{bot.node_name}] 切換原生語音客戶端失敗: {conn_err}")
                state.is_playing = False
                return
        else:
            state.is_playing = False
            return

    state.active_engine = "🔥 Native Studio Engine (384k VBR)"
    loop = asyncio.get_event_loop()
    stream_url, thumbnail, duration = await loop.run_in_executor(
        None, lambda: extract_stream_url_sync(next_song.url, next_song.title)
    )

    if not stream_url:
        print(f"[{bot.node_name}] 無法提取串流: {next_song.title}，自動嘗試下一首...")
        if state.text_channel:
            try:
                await state.text_channel.send(t('stream_failed', guild_id=guild.id, bot_name=bot.node_name, title=next_song.title))
            except Exception:
                pass
        await play_next(bot, guild)
        return

    if thumbnail and not next_song.thumbnail:
        next_song.thumbnail = thumbnail
    if duration and not next_song.duration:
        next_song.duration = duration

    try:
        ffmpeg_opts = get_ffmpeg_options(state)
        source = discord.FFmpegPCMAudio(stream_url, stderr=subprocess.DEVNULL, **ffmpeg_opts)

        audio_source = discord.PCMVolumeTransformer(source, volume=state.volume)

        def after_callback(error):
            if error:
                print(f"[{bot.node_name} Player Error] {error}")
            fut = asyncio.run_coroutine_threadsafe(play_next(bot, guild), bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"[{bot.node_name} Player Next Error] {e}")

        if not vc or not is_vc_connected(vc):
            print(f"[{bot.node_name}] 語音客戶端未連線，無法播放")
            state.is_playing = False
            return

        bitrate_kbps = 128
        if hasattr(vc, 'channel') and vc.channel and hasattr(vc.channel, 'bitrate') and vc.channel.bitrate:
            bitrate_kbps = min(384, max(64, vc.channel.bitrate // 1000))

        if hasattr(vc, 'play'):
            if asyncio.iscoroutinefunction(vc.play):
                print(f"[{bot.node_name}] 警告: vc 為非同步 Player，無法使用原生音源播放")
            else:
                vc.play(audio_source, after=after_callback)

        if state.text_channel:
            embed = build_nowplaying_embed(bot, next_song, state)
            view = MusicControllerView(bot, state)
            await state.text_channel.send(embed=embed, view=view)

    except Exception as e:
        print(f"[{bot.node_name} Play Error] 播放失敗: {e}")
        await play_next(bot, guild)


def get_bot_guild(bot: 'MusicNodeBot', guild_id: int, interaction_guild: Optional[discord.Guild] = None) -> Optional[discord.Guild]:
    if interaction_guild and interaction_guild.id == guild_id:
        if hasattr(bot, 'user') and bot.user and hasattr(interaction_guild, 'me') and interaction_guild.me:
            if interaction_guild.me.id == bot.user.id:
                return interaction_guild
        if getattr(bot, 'node_id', None) == 1:
            return interaction_guild

    g = bot.get_guild(guild_id)
    if g:
        return g
    g = discord.utils.get(bot.guilds, id=guild_id)
    if g:
        return g
    return None

async def get_or_fetch_bot_guild(bot: 'MusicNodeBot', guild_id: int, interaction_guild: Optional[discord.Guild] = None) -> Optional[discord.Guild]:
    g = get_bot_guild(bot, guild_id, interaction_guild)
    if g:
        return g
    try:
        fetched = await bot.fetch_guild(guild_id)
        if fetched:
            return fetched
    except Exception:
        pass
    return None

async def find_best_bot_for_channel(
    guild_id: int,
    voice_channel: discord.VoiceChannel,
    interaction_guild: Optional[discord.Guild] = None
) -> Tuple[Optional['MusicNodeBot'], bool]:
    available_bots = []
    for b in NODE_BOTS:
        guild = await get_or_fetch_bot_guild(b, guild_id, interaction_guild)
        if guild:
            available_bots.append((b, guild))

    if not available_bots and interaction_guild and NODE_BOTS:
        available_bots.append((NODE_BOTS[0], interaction_guild))

    if not available_bots:
        return None, False

    def get_vc(bot: 'MusicNodeBot', g: discord.Guild):
        if g and g.voice_client:
            return g.voice_client
        for v in getattr(bot, 'voice_clients', []):
            if v.guild and v.guild.id == guild_id:
                return v
        return None

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        if vc and is_vc_connected(vc) and hasattr(vc, 'channel') and vc.channel and vc.channel.id == voice_channel.id:
            return b, True

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        if not vc or not is_vc_connected(vc):
            return b, False

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        if vc and is_vc_connected(vc) and hasattr(vc, 'channel') and vc.channel:
            real_listeners = [m for m in vc.channel.members if not m.bot]
            if len(real_listeners) == 0:
                return b, False

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        state = b.get_state(guild_id)
        if vc and is_vc_connected(vc):
            if not is_vc_active(vc) and len(state.queue) == 0:
                return b, False

    return None, False

def get_bot_in_user_room(
    guild_id: int,
    user_voice_channel: discord.VoiceChannel,
    interaction_guild: Optional[discord.Guild] = None
) -> Optional['MusicNodeBot']:
    for b in NODE_BOTS:
        guild = get_bot_guild(b, guild_id, interaction_guild)
        if guild:
            vc = guild.voice_client
            if not vc:
                for v in getattr(b, 'voice_clients', []):
                    if v.guild and v.guild.id == guild.id:
                        vc = v
                        break
            if vc and is_vc_connected(vc) and hasattr(vc, 'channel') and vc.channel and vc.channel.id == user_voice_channel.id:
                return b
    return None

async def get_user_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    user_voice = getattr(interaction.user, 'voice', None)
    if user_voice and getattr(user_voice, 'channel', None):
        return user_voice.channel

    guild = interaction.guild
    if not guild:
        return None

    if isinstance(interaction.channel, (discord.VoiceChannel, discord.StageChannel)):
        return interaction.channel

    member = guild.get_member(interaction.user.id)
    if member and getattr(member, 'voice', None) and member.voice.channel:
        return member.voice.channel

    for vc in guild.voice_channels:
        if hasattr(vc, 'voice_states') and interaction.user.id in vc.voice_states:
            return vc
        if hasattr(vc, 'members') and any(m.id == interaction.user.id for m in vc.members):
            return vc

    try:
        fetched_member = await guild.fetch_member(interaction.user.id)
        if fetched_member and getattr(fetched_member, 'voice', None) and fetched_member.voice.channel:
            return fetched_member.voice.channel
    except Exception:
        pass

    return None


def register_slash_commands(primary_bot: commands.Bot):
    tree = primary_bot.tree

    @tree.command(name="play", description="智能點播歌曲（自動分流派發至空閒的機器人進房播放）")
    @app_commands.describe(
        查詢="歌曲名稱或影片/歌單網址",
        指定機器人="可選：指定派遣 1 ~ 5 號機器人 (留空則系統自動智能派工)"
    )
    async def slash_play(interaction: discord.Interaction, 查詢: str, 指定機器人: Optional[int] = None):
        await interaction.response.defer()

        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.followup.send(t('must_join_voice', interaction))
            return

        guild_id = interaction.guild_id

        selected_bot: Optional[MusicNodeBot] = None
        already_in_room = False

        if 指定機器人 is not None:
            for b in NODE_BOTS:
                if b.node_id == 指定機器人:
                    selected_bot = b
                    break
            if not selected_bot:
                await interaction.followup.send(f"❌ 找不到 【音樂 {指定機器人} 號】，目前上線的機器人數量為 {len(NODE_BOTS)} 隻！")
                return
        else:
            selected_bot, already_in_room = await find_best_bot_for_channel(guild_id, voice_channel, interaction.guild)

        if not selected_bot:
            guild_bots = []
            for b in NODE_BOTS:
                bg = get_bot_guild(b, guild_id, interaction.guild)
                if bg:
                    guild_bots.append((b, bg))

            total_active = len(guild_bots)
            unjoined_count = len(NODE_BOTS) - total_active

            if total_active <= 1:
                cur_b, cur_bg = guild_bots[0] if guild_bots else (NODE_BOTS[0], interaction.guild)
                cur_name = cur_b.node_name
                cur_vc = cur_bg.voice_client if cur_bg else None
                cur_room_name = f"`#{cur_vc.channel.name}`" if (cur_vc and hasattr(cur_vc, 'channel') and cur_vc.channel) else "其他房間"

                msg = t('only_one_bot_busy', interaction, bot_name=cur_name, room_name=cur_room_name)
                await interaction.followup.send(msg)
            else:
                await interaction.followup.send(t('no_bot_available', interaction))
            return

        bot_guild = get_bot_guild(selected_bot, guild_id, interaction.guild) or interaction.guild
        if not bot_guild:
            await interaction.followup.send(t('bot_not_in_guild', interaction, bot_name=selected_bot.node_name))
            return

        bot_voice_channel = bot_guild.get_channel(voice_channel.id) or voice_channel
        state = selected_bot.get_state(guild_id)
        state.text_channel = interaction.channel

        try:
            max_bitrate = bot_voice_channel.guild.bitrate_limit
            if bot_voice_channel.bitrate < max_bitrate:
                await bot_voice_channel.edit(bitrate=max_bitrate)
        except Exception:
            pass

        vc = bot_guild.voice_client
        if not vc:
            for v in getattr(selected_bot, 'voice_clients', []):
                if v.guild and v.guild.id == guild_id:
                    vc = v
                    break
        is_wavelink_player = HAS_WAVELINK and hasattr(vc, 'filters')
        should_be_wavelink = selected_bot.lavalink_connected and HAS_WAVELINK

        if vc and is_vc_connected(vc) and (is_wavelink_player != should_be_wavelink):
            await safe_disconnect(vc)
            vc = None

        if not vc or not is_vc_connected(vc):
            try:
                if should_be_wavelink:
                    try:
                        vc = await bot_voice_channel.connect(cls=wavelink.Player, self_deaf=True)
                    except Exception as wl_conn_err:
                        print(f"[{selected_bot.node_name}] Lavalink Player 連線異常 ({wl_conn_err})，使用原生語音連線...")
                        vc = await bot_voice_channel.connect(reconnect=True, timeout=30.0, self_deaf=True)
                else:
                    vc = await bot_voice_channel.connect(reconnect=True, timeout=30.0, self_deaf=True)
            except Exception as e:
                await interaction.followup.send(t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e)))
                return
        elif hasattr(vc, 'channel') and vc.channel and vc.channel.id != voice_channel.id:
            if not already_in_room:
                state.loop_mode = 'off'
                state.current = None
                state.queue.clear()
                state.is_playing = False
                await safe_stop(vc)
            await vc.move_to(bot_voice_channel)

        is_custom_music_service = bool(
            SPOTIFY_URL_REGEX.search(查詢) or
            NETEASE_URL_REGEX.search(查詢) or
            APPLE_MUSIC_URL_REGEX.search(查詢) or
            ("163cn.tv" in 查詢 or "163.lu" in 查詢 or "apple.co" in 查詢) or
            ("playlist" in 查詢.lower() or "list=" in 查詢.lower())
        )
        if selected_bot.lavalink_connected and HAS_WAVELINK and not is_custom_music_service:
            try:
                tracks: wavelink.Search = await wavelink.Playable.search(查詢)
                if tracks:
                    wl_track = tracks[0]
                    song = Song({
                        'title': wl_track.title,
                        'url': wl_track.uri or 查詢,
                        'duration': int(wl_track.length // 1000) if hasattr(wl_track, 'length') else 0,
                        'thumbnail': wl_track.artwork or '',
                        'uploader': wl_track.author or '未知'
                    }, interaction.user, wavelink_track=wl_track)

                    if is_vc_active(vc):
                        state.queue.append(song)
                        embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
                        await interaction.followup.send(embed=embed)
                    else:
                        state.queue.append(song)
                        if not already_in_room:
                            await interaction.followup.send(t('song_dispatched', interaction, title=song.title, bot_name=selected_bot.node_name, channel=voice_channel.mention))
                        else:
                            await interaction.followup.send(t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name))
                        await play_next(selected_bot, bot_guild)
                    return
            except Exception as e:
                print(f"[Slash Play] Lavalink 直通搜尋略過 ({e})，無縫使用原生解析管線...")

        loop = asyncio.get_event_loop()

        is_spotify = bool(SPOTIFY_URL_REGEX.search(查詢))
        is_netease = bool(NETEASE_URL_REGEX.search(查詢) or "163cn.tv" in 查詢 or "163.lu" in 查詢 or "music.163.com" in 查詢)
        is_apple = bool(APPLE_MUSIC_URL_REGEX.search(查詢) or "apple.co" in 查詢 or "music.apple.com" in 查詢)

        if is_spotify or is_netease or is_apple:
            ext_res = None
            if is_spotify:
                ext_res = await resolve_spotify(查詢)
                if ext_res:
                    ext_res.setdefault('platform', 'spotify')
                    ext_res.setdefault('platform_name', 'Spotify')
                    ext_res.setdefault('color', 0x1DB954)
            elif is_netease:
                ext_res = await resolve_netease(查詢)
            elif is_apple:
                ext_res = await resolve_apple_music(查詢)

            if not ext_res or not ext_res.get('tracks'):
                platform_label = "Spotify" if is_spotify else ("網易雲音樂" if is_netease else "Apple Music")
                await interaction.followup.send(f"❌ 無法解析該 {platform_label} 連結，請確認該歌曲或播放清單為公開狀態！")
                return

            platform_name = ext_res.get('platform_name', '音樂平台')
            embed_color = ext_res.get('color', 0x1DB954)

            if ext_res['type'] in ('playlist', 'album') or len(ext_res['tracks']) > 1:
                tracks = ext_res['tracks']
                playlist_title = ext_res.get('title', f"{platform_name} 播放清單")
                playlist_thumb = ext_res.get('thumbnail', '')
                added_count = 0

                for track_item in tracks[:200]:
                    t_title = track_item.get('title', '未知歌曲')
                    t_artist = track_item.get('artist', '')
                    title_str = f"{t_title} - {t_artist}" if t_artist else t_title
                    search_q = f"ytsearch5:{t_title} {t_artist}".strip()
                    song = Song({
                        'title': title_str,
                        'webpage_url': track_item.get('url') or 查詢,
                        'url': search_q,
                        'duration': track_item.get('duration', 0),
                        'thumbnail': track_item.get('thumbnail') or playlist_thumb,
                        'uploader': t_artist or platform_name
                    }, interaction.user)
                    state.queue.append(song)
                    added_count += 1

                type_label = "播放清單" if ext_res['type'] == 'playlist' else "專輯"
                embed = discord.Embed(
                    title=f"🎶 【{selected_bot.node_name}】成功匯入 {platform_name} {type_label}！",
                    description=f"**[{playlist_title}]({查詢})**",
                    color=embed_color
                )
                if playlist_thumb:
                    embed.set_thumbnail(url=playlist_thumb)
                embed.add_field(name="🔢 成功加入", value=f"`{added_count}` 首歌曲", inline=True)
                embed.add_field(name="📍 當前隊列長度", value=f"`{len(state.queue)}` 首", inline=True)
                embed.add_field(name="🤖 服務節點", value=f"`{selected_bot.node_name}`", inline=True)
                embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
                await interaction.followup.send(embed=embed)

                if not is_vc_active(vc):
                    await play_next(selected_bot, bot_guild)
                return

            track_item = ext_res['tracks'][0]
            t_title = track_item.get('title', '未知歌曲')
            t_artist = track_item.get('artist', '')
            title_str = f"{t_title} - {t_artist}" if t_artist else t_title
            search_q = f"ytsearch5:{t_title} {t_artist}".strip()

            yt_data = None
            try:
                data = await loop.run_in_executor(
                    None, lambda: yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True)).extract_info(search_q, download=False)
                )
                if data and 'entries' in data and data['entries']:
                    for entry in data['entries']:
                        if entry and (entry.get('url') or entry.get('formats')):
                            yt_data = entry
                            break
                elif data and (data.get('url') or data.get('formats')):
                    yt_data = data
            except Exception:
                yt_data = None

            if not yt_data:
                try:
                    sc_q = f"scsearch3:{t_title} {t_artist}".strip()
                    data = await loop.run_in_executor(
                        None, lambda: yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True)).extract_info(sc_q, download=False)
                    )
                    if data and 'entries' in data and data['entries']:
                        for entry in data['entries']:
                            if entry and (entry.get('url') or entry.get('formats')):
                                yt_data = entry
                                break
                    elif data and (data.get('url') or data.get('formats')):
                        yt_data = data
                except Exception:
                    yt_data = None

            if not yt_data:
                await interaction.followup.send(f"❌ 找不到該 {platform_name} 歌曲 **{title_str}** 的音源！")
                return

            yt_data['title'] = title_str
            yt_data['webpage_url'] = track_item.get('url') or 查詢
            if track_item.get('thumbnail'):
                yt_data['thumbnail'] = track_item['thumbnail']
            if track_item.get('duration'):
                yt_data['duration'] = track_item['duration']
            yt_data['uploader'] = t_artist or platform_name

            song = Song(yt_data, interaction.user)

            if is_vc_active(vc):
                state.queue.append(song)
                embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
                await interaction.followup.send(embed=embed)
            else:
                state.queue.append(song)
                if not already_in_room:
                    await interaction.followup.send(t('song_dispatched', interaction, title=song.title, bot_name=selected_bot.node_name, channel=voice_channel.mention))
                else:
                    await interaction.followup.send(t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name))
                await play_next(selected_bot, bot_guild)
            return

        is_playlist = ("playlist" in 查詢.lower()) or ("list=" in 查詢.lower())

        if is_playlist:
            try:
                data = await loop.run_in_executor(
                    None, lambda: yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=True, noplaylist=False)).extract_info(查詢, download=False)
                )
            except Exception as e:
                await interaction.followup.send(f"❌ 讀取播放清單失敗: `{e}`")
                return

            entries = data.get('entries', []) if data else []
            valid = [e for e in entries if e and (e.get('title') or e.get('url'))]
            if not valid:
                await interaction.followup.send("❌ 該播放清單中未找到任何有效歌曲！")
                return

            added_count = 0
            playlist_title = data.get('title', '播放清單')
            for e in valid[:200]:
                title = e.get('title', '未知歌曲')
                if title in ['[Deleted video]', '[Private video]']:
                    continue
                song_url = e.get('webpage_url') or e.get('url') or f"https://www.youtube.com/watch?v={e.get('id')}"
                song = Song({
                    'title': title,
                    'url': song_url,
                    'duration': e.get('duration', 0),
                    'thumbnail': e.get('thumbnail', '')
                }, interaction.user)
                state.queue.append(song)
                added_count += 1

            embed = discord.Embed(
                title=f"📑 【{selected_bot.node_name}】成功匯入播放清單！",
                description=f"**[{playlist_title}]({查詢})**",
                color=0x2ECC71
            )
            embed.add_field(name="🔢 成功加入", value=f"`{added_count}` 首歌曲", inline=True)
            embed.add_field(name="📍 當前隊列長度", value=f"`{len(state.queue)}` 首", inline=True)
            embed.add_field(name="🤖 服務節點", value=f"`{selected_bot.node_name}`", inline=True)
            embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed)

            if not is_vc_active(vc):
                await play_next(selected_bot, bot_guild)
            return

        try:
            data = await loop.run_in_executor(
                None, lambda: yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True)).extract_info(查詢, download=False)
            )
        except Exception as e:
            try:
                sc_q = f"scsearch:{查詢}"
                data = await loop.run_in_executor(
                    None, lambda: yt_dlp.YoutubeDL(get_ytdl_opts(extract_flat=False, noplaylist=True)).extract_info(sc_q, download=False)
                )
            except Exception:
                await interaction.followup.send(f"❌ 找不到該歌曲或解析失敗: `{e}`")
                return

        if 'entries' in data:
            if not data['entries']:
                await interaction.followup.send("❌ 搜尋結果為空，請更換關鍵字！")
                return
            data = data['entries'][0]

        song = Song(data, interaction.user)

        if is_vc_active(vc):
            state.queue.append(song)
            embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
            await interaction.followup.send(embed=embed)
        else:
            state.queue.append(song)
            if not already_in_room:
                await interaction.followup.send(t('song_dispatched', interaction, title=song.title, bot_name=selected_bot.node_name, channel=voice_channel.mention))
            else:
                await interaction.followup.send(t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name))
            await play_next(selected_bot, bot_guild)

    @tree.command(name="status", description="查看所有音樂機器人的即時連線、分流與伺服器進駐狀態儀表板")
    async def slash_status(interaction: discord.Interaction):
        guild_id = interaction.guild_id
        total_bots = len(NODE_BOTS)
        active_bots = 0
        playing_bots = 0

        for b in NODE_BOTS:
            bg = get_bot_guild(b, guild_id, interaction.guild)
            if bg:
                active_bots += 1
                g_vc = (bg.voice_client if bg else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=guild_id)
                if g_vc and is_vc_playing(g_vc):
                    playing_bots += 1

        load_pct = int((playing_bots / total_bots) * 100) if total_bots else 0
        load_bar = "▰" * (playing_bots * 2) + "▱" * ((total_bots - playing_bots) * 2)

        embed = discord.Embed(
            title="🎛️ DJ Donut — 機器人矩陣調度儀表板",
            description=f"**伺服器進駐節點: `{active_bots}/{total_bots}` 台**\n"
                        f"📊 **叢集負載率: `{load_pct}%`** `[{load_bar}]`\n"
                        f"⚡ 多房間獨立引擎: `48kHz Stereo / 384kbps VBR`\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
            color=0x5865F2
        )

        INVITE_URLS = {
            1: "https://discord.com/oauth2/authorize?client_id=1549028983641743462&permissions=8&integration_type=0&scope=bot",
            2: "https://discord.com/oauth2/authorize?client_id=1549258600155979956&permissions=8&integration_type=0&scope=bot",
            3: "https://discord.com/oauth2/authorize?client_id=1549259098761990184&permissions=8&integration_type=0&scope=bot",
            4: "https://discord.com/oauth2/authorize?client_id=1549259563646062743&permissions=8&integration_type=0&scope=bot",
            5: "https://discord.com/oauth2/authorize?client_id=1549259919696592976&permissions=8&integration_type=0&scope=bot",
        }

        for idx, b in enumerate(NODE_BOTS, start=1):
            guild = get_bot_guild(b, guild_id, interaction.guild)
            state = b.get_state(guild_id)
            g_vc = (guild.voice_client if guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=guild_id)

            if not guild:
                invite_link = INVITE_URLS.get(idx, "")
                link_text = f"> 🔗 [點此邀請 {idx} 號機器人進群]({invite_link})" if invite_link else "> 尚未加入此伺服器"
                embed.add_field(
                    name=f"⚪ 【{b.node_name}】未加入此伺服器",
                    value=link_text,
                    inline=False
                )
            elif g_vc and is_vc_connected(g_vc):
                vc_name = g_vc.channel.name if (hasattr(g_vc, 'channel') and g_vc.channel) else "Voice"
                is_play = is_vc_playing(g_vc)
                cur_title = state.current.title if state.current else "未知歌曲"
                cur_url = state.current.url if state.current else ""
                cur_dur = format_duration(state.current.duration) if (state.current and state.current.duration) else "LIVE"
                elapsed = format_duration(get_playback_elapsed(state))
                status_badge = "🟢 正在播放" if is_play else "🟡 暫停中"
                song_link = f"**[{cur_title[:32]}]({cur_url})**" if cur_url else f"**{cur_title[:32]}**"
                embed.add_field(
                    name=f"{status_badge} • {b.node_name}",
                    value=f"> 🔊 語音房間: **#{vc_name}**\n"
                          f"> 🎵 播放歌曲: {song_link} `[{elapsed}/{cur_dur}]`\n"
                          f"> 📊 待播隊列: `{len(state.queue)} 首` • 音量: `{int(state.volume * 100)}%`",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"🟢 【{b.node_name}】空閒待命中",
                    value="> 💤 節點已就緒，於任意語音房輸入 `/play` 將自動指派進房！",
                    inline=False
                )

        embed.set_footer(text=f"DJ Donut v{__version__} | Multi-Room Music Matrix | 支援 5 房獨立播放")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="help", description="查看 DJ Donut 現代化多房間音樂機器人使用指南與指令清單")
    async def slash_help(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎧 DJ Donut — 現代化音樂機器人使用指南",
            description="支援多房間無縫分流播放、高解析度進度條、即時動態歌詞與錄音室等化器。\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━",
            color=0x5865F2
        )
        embed.add_field(
            name="🎵 音樂播放指令",
            value="> `/play <查詢>` — 智慧點歌（自動派發空閒機器人進房）\n"
                  "> `/nowplaying` — 開啟進度條與全功能互動控制器\n"
                  "> `/queue` — 檢視當前語音房的待播歌單隊列\n"
                  "> `/skip` — 跳過當前歌曲\n"
                  "> `/stop` — 停止播放並讓機器人退出語音房",
            inline=False
        )
        embed.add_field(
            name="📻 電台與進階模式",
            value="> `/autoplay` — 開啟/關閉智慧自動接歌電台模式\n"
                  "> `/lyrics [歌曲]` — 查詢即時動態時間軸歌詞與純文字歌詞\n"
                  "> `/247` — 切換 24/7 永久常駐語音房模式",
            inline=False
        )
        embed.add_field(
            name="🎚️ 音質與錄音室等化器",
            value="> `/bassboost <強度>` — 即時調節 +4dB ~ +20dB 重低音\n"
                  "> `/filter <預設>` — 套用專業濾鏡 (搖滾/流行/人聲/夜核/8D等)\n"
                  "> `/bitrate [kbps]` — 查看或一鍵拉滿語音房最高音質 (至 384k)\n"
                  "> `/engine` — 雙引擎架構即時運作狀態與音訊規格檢視",
            inline=False
        )
        embed.add_field(
            name="🌐 叢集與語言設定",
            value="> `/status` — 查看 1～5 號機器人獨立運作與分流儀表板\n"
                  "> `/language <語系>` — 切換伺服器預設語言 (支援 6 國語系)",
            inline=False
        )
        embed.set_footer(text=f"DJ Donut v{__version__} • 支援 Spotify / 網易雲 / Apple Music / YouTube")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="skip", description="跳過你當前語音房的機器人歌曲")
    async def slash_skip(interaction: discord.Interaction):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if vc and is_vc_active(vc):
            if state.loop_mode == 'single':
                state.loop_mode = 'off'
            await safe_stop(vc)
            await interaction.response.send_message(t('skipped', interaction, bot_name=b.node_name))
        else:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)

    @tree.command(name="stop", description="停止音樂機器人並讓其離開語音房")
    @app_commands.describe(全部退出="是否強制讓本伺服器所有音樂機器人全數停止並退出語音房？")
    async def slash_stop(interaction: discord.Interaction, 全部退出: bool = False):
        guild_id = interaction.guild_id
        if 全部退出:
            stopped_names = []
            for b in NODE_BOTS:
                bg = get_bot_guild(b, guild_id, interaction.guild)
                if bg:
                    b_vc = bg.voice_client or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=guild_id)
                    if b_vc and is_vc_connected(b_vc):
                        b_state = b.get_state(guild_id)
                        b_state.loop_mode = 'off'
                        b_state.current = None
                        b_state.queue.clear()
                        b_state.is_playing = False
                        await safe_stop(b_vc)
                        await safe_disconnect(b_vc)
                        stopped_names.append(b.node_name)
            if stopped_names:
                await interaction.response.send_message(t('force_stopped_all', interaction, names=', '.join(stopped_names)))
            else:
                await interaction.response.send_message(t('no_bots_connected', interaction), ephemeral=True)
            return

        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.loop_mode = 'off'
        state.current = None
        state.queue.clear()
        state.is_playing = False
        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if vc:
            await safe_stop(vc)
            await safe_disconnect(vc)
        await interaction.response.send_message(t('stopped_and_left', interaction, bot_name=b.node_name))

    @tree.command(name="queue", description="查看你所在語音房的待播歌單")
    async def slash_queue(interaction: discord.Interaction):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        embed = build_queue_embed(b, state, lang=resolve_locale(interaction))
        await interaction.response.send_message(embed=embed)

    @tree.command(name="nowplaying", description="查看你所在語音房當前歌曲詳情與控制器")
    async def slash_nowplaying(interaction: discord.Interaction):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return

        embed = build_nowplaying_embed(b, state.current, state, lang=resolve_locale(interaction))
        view = MusicControllerView(b, state)
        await interaction.response.send_message(embed=embed, view=view)

    @tree.command(name="bitrate", description="查看或一鍵將當前語音房提升至伺服器最高音質位元率 (96k ~ 384k)")
    @app_commands.describe(指定位元率="可選：手動指定 kbps 數值 (例如 96, 128, 256, 384)；留空則自動升至伺服器最高上限")
    async def slash_bitrate(interaction: discord.Interaction, 指定位元率: Optional[int] = None):
        channel = await get_user_voice_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
            return
        guild = interaction.guild
        max_limit_bps = guild.bitrate_limit
        max_limit_kbps = max_limit_bps // 1000
        cur_kbps = channel.bitrate // 1000

        target_kbps = 指定位元率 if 指定位元率 is not None else max_limit_kbps
        target_bps = target_kbps * 1000

        if target_bps > max_limit_bps:
            await interaction.response.send_message(
                f"⚠️ 此伺服器等級最高支援 `{max_limit_kbps} kbps`！將為您調整為 `{max_limit_kbps} kbps`。",
                ephemeral=True
            )
            target_bps = max_limit_bps
            target_kbps = max_limit_kbps

        try:
            await channel.edit(bitrate=target_bps)
            embed = discord.Embed(
                title="🎚️ 語音房音質位元率已更新",
                color=0x2ECC71
            )
            embed.add_field(name="📍 語音頻道", value=channel.mention, inline=True)
            embed.add_field(name="📶 當前音質", value=f"`{target_kbps} kbps` (原: `{cur_kbps} kbps`)", inline=True)
            embed.add_field(name="🚀 伺服器上限", value=f"`{max_limit_kbps} kbps`", inline=True)
            embed.set_footer(text="💡 提示：聆聽音樂時，請關閉個人 Discord 設定的「Krisp 降噪」避免背景樂器被過濾！")
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="⚠️ 機器人缺少「管理頻道」權限",
                description=(
                    f"目前語音頻道 **{channel.name}** 的位元率為 `{cur_kbps} kbps`。\n\n"
                    f"**如何手動開啟極致音質？**\n"
                    f"1. 請伺服器管理員右鍵點擊頻道 -> **編輯頻道** -> **概覽**\n"
                    f"2. 將「**位元率**」滑桿直接拉至最右側最高 `{max_limit_kbps} kbps`\n"
                    f"3. 或在伺服器身分組中給予機器人「**管理頻道**」權限，機器人即可自動替您拉滿！"
                ),
                color=0xE67E22
            )
            embed.set_footer(text="💡 聽歌時請同時關閉個人 Discord「Krisp 降噪」以獲得最佳立體聲效果！")
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ 調整失敗: `{e}`", ephemeral=True)

    @tree.command(name="bassboost", description="即時調整或開啟重低音增強 (Bass Boost)")
    @app_commands.describe(等級="選擇重低音增強強度")
    @app_commands.choices(等級=[
        app_commands.Choice(name="❌ 關閉重低音", value="off"),
        app_commands.Choice(name="🍃 輕微低音 (+4dB)", value="low"),
        app_commands.Choice(name="🔊 飽滿重低音 (+8dB)", value="medium"),
        app_commands.Choice(name="💥 狂暴重低音 (+14dB)", value="high"),
        app_commands.Choice(name="🔥 震動全場 / 極致重低音 (+20dB)", value="extreme")
    ])
    async def slash_bassboost(interaction: discord.Interaction, 等級: app_commands.Choice[str]):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message("❌ 你所在的語音房內目前沒有任何音樂機器人！", ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.bassboost = 等級.value
        choice_label = 等級.name

        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if b.lavalink_connected and HAS_WAVELINK and vc and hasattr(vc, 'filters'):
            try:
                filters = vc.filters
                bb_gain = BASSBOOST_PRESETS.get(state.bassboost, {}).get("gain", 0.0)
                if bb_gain > 0:
                    filters.equalizer.set(bands=[
                        {"band": 0, "gain": bb_gain},
                        {"band": 1, "gain": bb_gain * 0.8},
                        {"band": 2, "gain": bb_gain * 0.5},
                        {"band": 3, "gain": bb_gain * 0.2}
                    ])
                else:
                    filters.equalizer.reset()
                await vc.set_filters(filters)
                await interaction.response.send_message(f"⚡ 【{b.node_name}】Lavalink 32-bit DSP 重低音已**毫秒級即時生效**：**{choice_label}**！")
                return
            except Exception as e:
                print(f"[Bassboost Error] {e}")

        await interaction.response.send_message(f"🎛️ 【{b.node_name}】重低音已設為：**{choice_label}**！\n*(極限原生 FFmpeg 引擎將於切換或下首歌曲以 384k 精度套用)*")

    @tree.command(name="filter", description="套用專業等化器音效濾鏡 (搖滾/流行/人聲/高音/Nightcore/8D等)")
    @app_commands.describe(音效="選擇要套用的音效預設")
    @app_commands.choices(音效=[
        app_commands.Choice(name="🎵 原音直通 (重置所有濾波)", value="reset"),
        app_commands.Choice(name="🔊 重低音強化 (Bass Boost)", value="bass"),
        app_commands.Choice(name="🎸 搖滾動態 (V型強化)", value="rock"),
        app_commands.Choice(name="🎤 流行樂 (人聲中頻溫暖)", value="pop"),
        app_commands.Choice(name="✨ 清澈人聲 (凸顯主唱與細節)", value="vocal"),
        app_commands.Choice(name="💎 通透高音 (空靈清脆感)", value="treble"),
        app_commands.Choice(name="⚡ 夜核 Nightcore (加速升調)", value="nightcore"),
        app_commands.Choice(name="🌊 蒸氣波 Vaporwave (慢速復古)", value="vaporwave"),
        app_commands.Choice(name="🎧 8D 空間環繞立體聲", value="8d")
    ])
    async def slash_filter(interaction: discord.Interaction, 音效: app_commands.Choice[str]):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message("❌ 你所在的語音房內目前沒有任何音樂機器人！", ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.filter = 音效.value
        choice_label = 音效.name

        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if b.lavalink_connected and HAS_WAVELINK and vc and hasattr(vc, 'filters'):
            try:
                filters = vc.filters
                if state.filter == "nightcore":
                    filters.timescale.set(speed=1.25, pitch=1.25)
                elif state.filter == "vaporwave":
                    filters.timescale.set(speed=0.85, pitch=0.8)
                elif state.filter == "8d":
                    filters.rotation.set(hertz=0.2)
                elif state.filter == "reset":
                    filters.reset()
                await vc.set_filters(filters)
                await interaction.response.send_message(f"⚡ 【{b.node_name}】Lavalink 專業音效濾鏡已**毫秒級即時生效**：**{choice_label}**！")
                return
            except Exception as e:
                print(f"[Filter Error] {e}")

        await interaction.response.send_message(f"🎛️ 【{b.node_name}】音效濾波器已切換至：**{choice_label}**！\n*(極限原生 FFmpeg 引擎將以 384k 錄音室級編碼套用)*")

    @tree.command(name="engine", description="查看目前音訊引擎運作狀態 (Lavalink / 原生 FFmpeg)")
    async def slash_engine(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎧 音訊引擎狀態 (Audio Engine Status)",
            description="DJ Donut 支援 Lavalink v4 與本機原生 FFmpeg 雙模音訊管線：",
            color=0x9B59B6
        )

        lavalink_connected = any(b.lavalink_connected for b in NODE_BOTS)
        lavalink_status = "🟢 已連線 (使用 Lavalink v4 節點)" if lavalink_connected else "⚪ 未連線 (使用本機原生 FFmpeg)"
        node_uri_display = LAVALINK_URI if LAVALINK_URI else "未配置 (可於 .env 設定 LAVALINK_URI)"

        embed.add_field(
            name="⚡ Lavalink v4",
            value=f"• 狀態: `{lavalink_status}`\n• 節點位址: `{node_uri_display}`\n• 支援: 外部音訊節點轉發、即時等化器濾鏡",
            inline=False
        )
        embed.add_field(
            name="🎵 原生 FFmpeg 引擎",
            value="• 狀態: `🟢 就緒`\n• 支援: 最高 384kbps Opus 編碼、預載緩衝區、單執行緒 CPU 保護",
            inline=False
        )
        embed.add_field(name="🤖 機器人數量", value=f"`共 {len(NODE_BOTS)} 隻待命中`", inline=True)
        embed.set_footer(text="提示：可使用 /bassboost 或 /filter 調整音效等化器")
        await interaction.response.send_message(embed=embed)

    @tree.command(name="autoplay", description="開啟或關閉智慧自動接歌電台模式 (Toggle Autoplay radio mode)")
    async def slash_autoplay(interaction: discord.Interaction):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.autoplay = not state.autoplay
        if state.autoplay:
            msg = t('autoplay_enabled', interaction, bot_name=b.node_name)
        else:
            msg = t('autoplay_disabled', interaction, bot_name=b.node_name)
        await interaction.response.send_message(msg)

    @tree.command(name="247", description="切換機器人 24/7 永久常駐模式 (Toggle 24/7 permanence mode)")
    async def slash_247(interaction: discord.Interaction):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.mode_247 = not state.mode_247
        if state.mode_247:
            msg = t('mode_247_enabled', interaction, bot_name=b.node_name)
        else:
            msg = t('mode_247_disabled', interaction, bot_name=b.node_name)
        await interaction.response.send_message(msg)

    @tree.command(name="lyrics", description="查詢當前或指定歌曲的即時動態歌詞 (Synchronized / plain lyrics)")
    @app_commands.describe(歌曲="可選：手動輸入要搜尋的歌曲名稱 (留空則搜尋當前播放中的歌曲)")
    async def slash_lyrics(interaction: discord.Interaction, 歌曲: Optional[str] = None):
        await interaction.response.defer()
        target_title = 歌曲
        target_artist = ""

        if not target_title:
            voice_channel = await get_user_voice_channel(interaction)
            b = None
            if voice_channel:
                b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
            if b:
                state = b.get_state(interaction.guild_id)
                if state.current:
                    target_title = state.current.title
                    target_artist = state.current.uploader or ""

        if not target_title:
            await interaction.followup.send(t('not_playing', interaction))
            return

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, lambda: get_lyrics(target_title, target_artist))

        if not res:
            await interaction.followup.send(t('lyrics_not_found', interaction, title=target_title))
            return

        display_title, lrc_text, is_synced = res
        if len(lrc_text) > 4000:
            lrc_text = lrc_text[:3990] + "\n..."

        embed = discord.Embed(
            title=t('lyrics_title', interaction, title=display_title),
            description=lrc_text,
            color=0x1DB954 if is_synced else 0x3498DB
        )
        sync_str = "⚡ 動態時間軸 (Synced)" if is_synced else "📝 純文字歌詞 (Plain)"
        embed.set_footer(text=f"模式: {sync_str} | 資料庫: LRCLIB / Netease")
        await interaction.followup.send(embed=embed)

    @tree.command(name="language", description="設定伺服器預設語言 (Set default language for this server)")
    @app_commands.describe(語言="選擇偏好的顯示語言 (Choose preferred language)")
    @app_commands.choices(語言=[
        app_commands.Choice(name="繁體中文 🇹🇼", value="zh-TW"),
        app_commands.Choice(name="English 🇺🇸", value="en"),
        app_commands.Choice(name="简体中文 🇨🇳", value="zh-CN"),
        app_commands.Choice(name="日本語 🇯🇵", value="ja"),
        app_commands.Choice(name="한국어 🇰🇷", value="ko"),
        app_commands.Choice(name="Español 🇪🇸", value="es"),
    ])
    async def slash_language(interaction: discord.Interaction, 語言: app_commands.Choice[str]):
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ 此指令僅能在伺服器頻道中使用！", ephemeral=True)
            return

        GUILD_LANGUAGES[guild_id] = 語言.value
        for b in NODE_BOTS:
            state = b.get_state(guild_id)
            state.lang = 語言.value

        await interaction.response.send_message(
            t('language_updated', interaction, guild_id=guild_id, lang=語言.value, lang_name=語言.name)
        )

    @tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        print(f"[Master Tree Error] {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ 執行錯誤: {error}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ 執行錯誤: {error}", ephemeral=True)
        except Exception:
            pass

async def main():
    if not TOKENS:
        print("\n" + "!" * 65)
        print("【錯誤】未找到任何有效的機器人 Token！")
        print("請打開 .env 檔案並填入 1 ~ 5 隻機器人的 Token：")
        print("BOT_TOKEN_1=你的第1隻機器人Token")
        print("BOT_TOKEN_2=你的第2隻機器人Token")
        print("...")
        print("BOT_TOKEN_5=你的第5隻機器人Token")
        print("!" * 65 + "\n")
        return

    print("=" * 65)
    print(f"🚀 正在初始化多房間音樂機器人矩陣 v{__version__} (共檢測到 {len(TOKENS)} 隻機器人配置)...")
    print("=" * 65)

    tasks = []
    for node_id, token in TOKENS:
        node_bot = MusicNodeBot(node_id)
        NODE_BOTS.append(node_bot)
        tasks.append(node_bot.start(token))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n所有音樂機器人節點已被手動關閉。")
