import os
import re
import logging
from typing import Any, Dict, List, Tuple
from dotenv import load_dotenv

load_dotenv()

__version__ = "1.7.0"

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

class AudioSourceError(AudioError):
    pass

class StreamExtractionError(AudioError):
    pass

class TrackNotFoundError(BotError):
    pass

class VoiceConnectionError(BotError):
    pass

class PermissionDeniedError(BotError):
    pass

wavelink: Any
try:
    import wavelink as _wavelink
    wavelink = _wavelink
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
COOKIES_FILE = COOKIE_FILE

BASSBOOST_PRESETS: Dict[str, Dict[str, Any]] = {
    "off": {"label": "關閉 (原音)", "gain": 0.0, "ffmpeg": None},
    "low": {"label": "輕度 (+4dB)", "gain": 0.15, "ffmpeg": "bass=g=4:f=110:w=0.6"},
    "medium": {"label": "中度 (+8dB)", "gain": 0.30, "ffmpeg": "bass=g=8:f=110:w=0.6"},
    "high": {"label": "強力 (+14dB)", "gain": 0.50, "ffmpeg": "bass=g=14:f=90:w=0.7"},
    "extreme": {"label": "極限轟炸 (+20dB)", "gain": 0.80, "ffmpeg": "bass=g=20:f=80:w=0.8"}
}

EQ_PRESETS: Dict[str, Dict[str, Any]] = {
    "reset": {"label": "原音直通 (Flat)", "ffmpeg": None},
    "bass": {"label": "🔊 重低音強化 (Bass Boost)", "ffmpeg": "bass=g=8:f=110:w=0.6"},
    "rock": {"label": "🎸 搖滾 (Rock)", "ffmpeg": "firequalizer=gain_entry='entry(60,4);entry(170,3);entry(310,0);entry(600,-2);entry(1000,-1);entry(3000,1);entry(6000,3);entry(12000,4);entry(14000,4);entry(16000,4)'"},
    "pop": {"label": "🎤 流行 (Pop)", "ffmpeg": "firequalizer=gain_entry='entry(60,-1);entry(170,1);entry(310,3);entry(600,4);entry(1000,4);entry(3000,2);entry(6000,0);entry(12000,-1);entry(14000,-1);entry(16000,-1)'"},
    "vocal": {"label": "🎙️ 人聲清晰 (Vocal Boost)", "ffmpeg": "firequalizer=gain_entry='entry(60,-3);entry(170,-1);entry(310,1);entry(600,3);entry(1000,4);entry(3000,4);entry(6000,3);entry(12000,1);entry(14000,0);entry(16000,-1)'"},
    "treble": {"label": "✨ 高音增強 (Treble)", "ffmpeg": "treble=g=5:f=4000:w=0.7"},
    "nightcore": {"label": "⚡ Nightcore (加速高音)", "ffmpeg": "asetrate=48000*1.25,aresample=48000,atempo=1.0"},
    "vaporwave": {"label": "🌆 Vaporwave (降速蒸氣)", "ffmpeg": "asetrate=48000*0.85,aresample=48000,atempo=1.0"},
    "8d": {"label": "🎧 8D 空間環繞立體聲", "ffmpeg": "apulsator=hz=0.125"}
}
AUDIO_FILTER_PRESETS = EQ_PRESETS

STREAM_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

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


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read an integer env var, falling back to ``default`` on bad input."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Invalid integer for %s=%r, using default %s", name, raw, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Runtime tunables (all optional, see .env.example)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.getenv("DATA_DIR", "").strip() or os.path.join(BASE_DIR, "data"))
DB_FILE = os.path.join(DATA_DIR, "donut.db")
LEGACY_SETTINGS_FILE = os.path.join(BASE_DIR, "guild_settings.json")

# Only enable for a self-signed Lavalink node. Never disable verification globally.
LAVALINK_INSECURE_TLS = _env_bool("LAVALINK_INSECURE_TLS", False)

MEDIA_WORKERS = _env_int("MEDIA_WORKERS", 6, minimum=1)
MEDIA_TIMEOUT = _env_int("MEDIA_TIMEOUT", 60, minimum=5)
MAX_QUEUE_SIZE = _env_int("MAX_QUEUE_SIZE", 500, minimum=1)
COMMAND_COOLDOWN = _env_int("COMMAND_COOLDOWN", 3, minimum=0)
RESTORE_QUEUES = _env_bool("RESTORE_QUEUES", True)
QUEUE_SNAPSHOT_INTERVAL = _env_int("QUEUE_SNAPSHOT_INTERVAL", 30, minimum=5)

METRICS_HOST = os.getenv("METRICS_HOST", "127.0.0.1").strip() or "127.0.0.1"
METRICS_PORT = _env_int("METRICS_PORT", 8080)  # 0 disables the HTTP endpoint

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "").strip()
