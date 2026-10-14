import os
import sys
import json
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, List, Tuple, Any
import aiohttp
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

__version__ = "1.4.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DJDonut")

class BotError(Exception):
    pass

class AudioError(BotError):
    pass

class StreamExtractionError(BotError):
    pass

class VoiceConnectionError(BotError):
    pass

try:
    import wavelink
    HAS_WAVELINK = True
except ImportError:
    wavelink = None
    HAS_WAVELINK = False

LAVALINK_URI = os.getenv("LAVALINK_URI", "").strip()
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass").strip()

TOKENS: List[Tuple[int, str]] = []
for i in range(1, 6):
    t_val = os.getenv(f"BOT_TOKEN_{i}")
    if t_val and t_val.strip() and t_val != "YOUR_BOT_TOKEN_HERE" and not t_val.startswith("YOUR_BOT_TOKEN_"):
        TOKENS.append((i, t_val.strip()))

if not TOKENS:
    legacy = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
    if legacy and legacy.strip() and legacy != "YOUR_BOT_TOKEN_HERE" and not legacy.startswith("YOUR_BOT_TOKEN_"):
        TOKENS.append((1, legacy.strip()))

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "guild_settings.json")
_SETTINGS_LOCK = threading.Lock()

BASSBOOST_PRESETS = {
    "off": {"label": "關閉 (原音)", "gain": 0.0, "ffmpeg": None},
    "low": {"label": "輕度 (+4dB)", "gain": 0.15, "ffmpeg": "bass=g=4:f=110:w=0.6"},
    "medium": {"label": "中度 (+8dB)", "gain": 0.30, "ffmpeg": "bass=g=8:f=110:w=0.6"},
    "high": {"label": "強力 (+14dB)", "gain": 0.50, "ffmpeg": "bass=g=14:f=90:w=0.7"},
    "extreme": {"label": "極限轟炸 (+20dB)", "gain": 0.80, "ffmpeg": "bass=g=20:f=80:w=0.8"}
}

EQ_PRESETS = {
    "reset": {"label": "原音直通 (Flat)", "ffmpeg": None},
    "rock": {"label": "🎸 搖滾 (Rock)", "ffmpeg": "firequalizer=gain_entry='entry(60,4);entry(170,3);entry(310,0);entry(600,-2);entry(1000,-1);entry(3000,1);entry(6000,3);entry(12000,4);entry(14000,4);entry(16000,4)'"},
    "pop": {"label": "🎤 流行 (Pop)", "ffmpeg": "firequalizer=gain_entry='entry(60,-1);entry(170,1);entry(310,3);entry(600,4);entry(1000,4);entry(3000,2);entry(6000,0);entry(12000,-1);entry(14000,-1);entry(16000,-1)'"},
    "vocal": {"label": "🎙️ 人聲清晰 (Vocal Boost)", "ffmpeg": "firequalizer=gain_entry='entry(60,-3);entry(170,-1);entry(310,1);entry(600,3);entry(1000,4);entry(3000,4);entry(6000,3);entry(12000,1);entry(14000,0);entry(16000,-1)'"},
    "treble": {"label": "✨ 高音增強 (Treble)", "ffmpeg": "treble=g=5:f=4000:w=0.7"},
    "nightcore": {"label": "⚡ Nightcore (加速高音)", "ffmpeg": "asetrate=48000*1.25,aresample=48000,atempo=1.0"},
    "vaporwave": {"label": "🌆 Vaporwave (降速蒸氣)", "ffmpeg": "asetrate=48000*0.85,aresample=48000,atempo=1.0"},
    "8d": {"label": "🎧 8D 空間環繞立體聲", "ffmpeg": "apulsator=hz=0.125"}
}

STREAM_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

class YTDLNullLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

def get_ytdl_opts(extract_flat: bool = False, noplaylist: bool = True) -> dict:
    opts = {
        'format': 'bestaudio[format_id=251]/bestaudio[ext=webm][acodec=opus][abr>=160]/bestaudio[ext=m4a][abr>=128]/bestaudio/best',
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
        'socket_timeout': 15,
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'mweb'],
                'skip': ['hls']
            }
        },
        'http_headers': {
            'User-Agent': STREAM_USER_AGENT,
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9'
        }
    }
    if extract_flat:
        opts['extract_flat'] = True
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE
    return opts

MEDIA_EXECUTOR = ThreadPoolExecutor(max_workers=10, thread_name_prefix='media_worker')
_LOCAL_STORAGE = threading.local()

def get_thread_ytdl(extract_flat: bool = False, noplaylist: bool = True) -> yt_dlp.YoutubeDL:
    key = f"ytdl_{extract_flat}_{noplaylist}"
    if not hasattr(_LOCAL_STORAGE, key):
        opts = get_ytdl_opts(extract_flat=extract_flat, noplaylist=noplaylist)
        setattr(_LOCAL_STORAGE, key, yt_dlp.YoutubeDL(opts))
    return getattr(_LOCAL_STORAGE, key)

STREAM_CACHE: Dict[str, Tuple[str, Optional[str], int, float]] = {}
STREAM_CACHE_LOCK = threading.Lock()

def get_cached_stream(key: str) -> Optional[Tuple[str, Optional[str], int]]:
    now = time.time()
    with STREAM_CACHE_LOCK:
        if key in STREAM_CACHE:
            stream_url, thumb, dur, expire_at = STREAM_CACHE[key]
            if expire_at > now:
                return stream_url, thumb, dur
            del STREAM_CACHE[key]
    return None

def set_cached_stream(key: str, stream_url: str, thumb: Optional[str], dur: int, ttl: int = 1800):
    now = time.time()
    with STREAM_CACHE_LOCK:
        if len(STREAM_CACHE) > 500:
            expired_keys = [k for k, v in STREAM_CACHE.items() if v[3] <= now]
            for k in expired_keys:
                del STREAM_CACHE[k]
            if len(STREAM_CACHE) > 500:
                STREAM_CACHE.pop(next(iter(STREAM_CACHE)), None)
        STREAM_CACHE[key] = (stream_url, thumb, dur, now + ttl)

METADATA_CACHE: Dict[str, Tuple[dict, float]] = {}
METADATA_CACHE_LOCK = threading.Lock()

def get_cached_metadata(key: str) -> Optional[dict]:
    now = time.time()
    with METADATA_CACHE_LOCK:
        if key in METADATA_CACHE:
            data, expire_at = METADATA_CACHE[key]
            if expire_at > now:
                return data
            del METADATA_CACHE[key]
    return None

def set_cached_metadata(key: str, data: dict, ttl: int = 3600):
    now = time.time()
    with METADATA_CACHE_LOCK:
        if len(METADATA_CACHE) > 1000:
            expired_keys = [k for k, v in METADATA_CACHE.items() if v[1] <= now]
            for k in expired_keys:
                del METADATA_CACHE[k]
            if len(METADATA_CACHE) > 1000:
                METADATA_CACHE.pop(next(iter(METADATA_CACHE)), None)
        METADATA_CACHE[key] = (data, now + ttl)

_HTTP_SESSION: Optional[aiohttp.ClientSession] = None

async def get_http_session() -> aiohttp.ClientSession:
    global _HTTP_SESSION
    if _HTTP_SESSION is None or _HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300, ssl=False)
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        _HTTP_SESSION = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _HTTP_SESSION

async def close_http_session():
    global _HTTP_SESSION
    if _HTTP_SESSION and not _HTTP_SESSION.closed:
        await _HTTP_SESSION.close()
        _HTTP_SESSION = None

def load_all_guild_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_guild_setting(guild_id: int, key: str, value: Any):
    with _SETTINGS_LOCK:
        try:
            data = load_all_guild_settings()
            gid = str(guild_id)
            if gid not in data:
                data[gid] = {}
            data[gid][key] = value
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def parse_time_str(time_str: str) -> Optional[int]:
    time_str = time_str.strip()
    if time_str.isdigit():
        return int(time_str)
    parts = time_str.split(':')
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None

def format_duration(seconds: Optional[int], lang: str = 'zh-TW') -> str:
    if not seconds:
        return "Live / Unknown" if lang == 'en' else "直播 / 未知"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins:02d}:{secs:02d}"

def get_ffmpeg_options(state: Any, seek_seconds: Optional[int] = None) -> dict:
    before_parts = [
        '-nostats -loglevel fatal',
        f'-headers "User-Agent: {STREAM_USER_AGENT}\r\nAccept: */*\r\nAccept-Language: en-US,en;q=0.9\r\n"',
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1 -reconnect_on_http_error 4xx,5xx',
        '-probesize 64k -analyzeduration 500000'
    ]
    if seek_seconds and seek_seconds > 0:
        before_parts.insert(1, f"-ss {seek_seconds}")
    before = " ".join(before_parts)
    filters = []
    bb = (BASSBOOST_PRESETS.get(getattr(state, 'bassboost', 'off')) or {}).get("ffmpeg")
    if bb:
        filters.append(bb)
    eq = (EQ_PRESETS.get(getattr(state, 'filter', 'reset')) or {}).get("ffmpeg")
    if eq:
        filters.append(eq)

    if filters:
        opt = f"-vn -af \"{','.join(filters)}\""
    else:
        opt = "-vn"
    return {'before_options': before, 'options': opt}
