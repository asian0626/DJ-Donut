"""yt-dlp wiring and the bounded worker pool used for blocking media work."""
import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

import yt_dlp

from settings import COOKIE_FILE, MEDIA_TIMEOUT, MEDIA_WORKERS, STREAM_USER_AGENT

T = TypeVar("T")

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
        'ignoreerrors': True,
        'quiet': True,
        'no_warnings': True,
        'logger': YTDLNullLogger(),
        'default_search': 'ytsearch',
        'source_address': '0.0.0.0',
        'socket_timeout': 15,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios']
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

MEDIA_EXECUTOR = ThreadPoolExecutor(max_workers=MEDIA_WORKERS, thread_name_prefix='media_worker')
_LOCAL_STORAGE = threading.local()


def get_thread_ytdl(extract_flat: bool = False, noplaylist: bool = True) -> yt_dlp.YoutubeDL:
    key = f"ytdl_{extract_flat}_{noplaylist}"
    if not hasattr(_LOCAL_STORAGE, key):
        opts = get_ytdl_opts(extract_flat=extract_flat, noplaylist=noplaylist)
        setattr(_LOCAL_STORAGE, key, yt_dlp.YoutubeDL(opts))
    return getattr(_LOCAL_STORAGE, key)


async def run_media(func: Callable[..., T], timeout: float = MEDIA_TIMEOUT) -> T:
    """Run blocking yt-dlp/spotipy work on the bounded pool with a hard timeout.

    The pool size caps concurrent extractions (memory / CPU), and the timeout
    keeps a stuck extractor from hanging a slash command forever.
    """
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(MEDIA_EXECUTOR, func), timeout=timeout)
