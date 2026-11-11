"""Backwards-compatible facade.

The old monolithic ``config.py`` mixed env parsing, caches, yt-dlp, HTTP,
persistence and ffmpeg helpers. Those now live in focused modules
(``settings``, ``cache``, ``ytdl``, ``httpclient``, ``storage``, ``utils``);
this module simply re-exports their public names so existing imports keep
working.
"""
from settings import *
from settings import (
    APPLE_MUSIC_URL_REGEX,
    AUDIO_FILTER_PRESETS,
    BASSBOOST_PRESETS,
    COMMAND_COOLDOWN,
    COOKIE_FILE,
    COOKIES_FILE,
    EQ_PRESETS,
    FORCE_LAVALINK,
    HAS_WAVELINK,
    LAVALINK_INSECURE_TLS,
    LAVALINK_PASSWORD,
    LAVALINK_URI,
    MAX_QUEUE_SIZE,
    NETEASE_URL_REGEX,
    QUEUE_SNAPSHOT_INTERVAL,
    RESTORE_QUEUES,
    SENTRY_DSN,
    SPOTIFY_URL_REGEX,
    STREAM_USER_AGENT,
    TOKENS,
    AudioError,
    AudioSourceError,
    BotError,
    PermissionDeniedError,
    StreamExtractionError,
    TrackNotFoundError,
    VoiceConnectionError,
    __version__,
    logger,
    wavelink,
)
from cache import (
    clear_caches,
    get_cached_autocomplete,
    get_cached_lyrics,
    get_cached_metadata,
    get_cached_stream,
    invalidate_cached_stream,
    is_stream_url_expired,
    set_cached_autocomplete,
    set_cached_lyrics,
    set_cached_metadata,
    set_cached_stream,
    stream_cache_key,
)
from ytdl import MEDIA_EXECUTOR, get_thread_ytdl, get_ytdl_opts, run_media
from httpclient import close_http_session, get_http_session
from storage import (
    add_user_favorite,
    clear_user_favorites,
    get_guild_setting,
    get_user_favorites,
    load_all_guild_settings,
    load_guild_settings,
    remove_user_favorite,
    save_guild_setting,
)
from utils import format_duration, get_ffmpeg_options, parse_time_str
