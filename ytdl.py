"""yt-dlp wiring and the bounded worker pool used for blocking media work."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar
from weakref import WeakKeyDictionary, WeakValueDictionary

import yt_dlp

from settings import COOKIE_FILE, MEDIA_TIMEOUT, MEDIA_WORKERS, STREAM_USER_AGENT, logger

T = TypeVar("T")


class YTDLNullLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def get_ytdl_opts(extract_flat: bool = False, noplaylist: bool = True) -> dict:
    opts = {
        "format": "bestaudio[format_id=251]/bestaudio[ext=webm][acodec=opus][abr>=160]/bestaudio[ext=m4a][abr>=128]/bestaudio/best",
        "extractaudio": True,
        "audioquality": "0",
        "restrictfilenames": True,
        "noplaylist": noplaylist,
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": True,
        "logger": YTDLNullLogger(),
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
        "socket_timeout": 15,
        "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
        "http_headers": {"User-Agent": STREAM_USER_AGENT, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"},
    }
    if extract_flat:
        opts["extract_flat"] = True
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    return opts


MEDIA_EXECUTOR = ThreadPoolExecutor(max_workers=MEDIA_WORKERS, thread_name_prefix="media_worker")
API_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="metadata_worker")
_LIMITERS: WeakKeyDictionary = WeakKeyDictionary()
_LOCAL_STORAGE = threading.local()


def get_thread_ytdl(extract_flat: bool = False, noplaylist: bool = True) -> yt_dlp.YoutubeDL:
    key = f"ytdl_{extract_flat}_{noplaylist}"
    if not hasattr(_LOCAL_STORAGE, key):
        opts = get_ytdl_opts(extract_flat=extract_flat, noplaylist=noplaylist)
        setattr(_LOCAL_STORAGE, key, yt_dlp.YoutubeDL(opts))
    return getattr(_LOCAL_STORAGE, key)


async def _run_bounded(func: Callable[..., T], executor: ThreadPoolExecutor, workers: int, timeout: float) -> T:
    loop = asyncio.get_running_loop()
    key = (executor, workers)
    limiters = _LIMITERS.setdefault(loop, WeakValueDictionary())
    limiter = limiters.setdefault(key, asyncio.Semaphore(workers))

    async def submit() -> T:
        await limiter.acquire()
        try:
            future = loop.run_in_executor(executor, func)
        except BaseException:
            limiter.release()
            raise

        # shield keeps the permit until the real worker finishes, even after caller timeout.
        def finished(done: asyncio.Future) -> None:
            limiter.release()
            if not done.cancelled():
                done.exception()

        future.add_done_callback(finished)
        return await asyncio.shield(future)

    try:
        return await asyncio.wait_for(submit(), timeout=timeout)
    except asyncio.TimeoutError:
        from metrics import inc

        inc("background_timeouts_total")
        logger.warning("Background operation exceeded %.1fs; running workers retain capacity until completion", timeout)
        raise


async def run_media(func: Callable[..., T], timeout: float = MEDIA_TIMEOUT) -> T:
    return await _run_bounded(func, MEDIA_EXECUTOR, MEDIA_WORKERS, timeout)


async def run_metadata(func: Callable[..., T], timeout: float = 20.0) -> T:
    return await _run_bounded(func, API_EXECUTOR, 2, timeout)
