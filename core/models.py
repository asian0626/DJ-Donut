import asyncio
from collections import deque
import queue
import re
import threading
import time
from typing import Any, Optional, Tuple

import discord

from config import (
    BASSBOOST_PRESETS,
    EQ_PRESETS,
    MAX_QUEUE_SIZE,
    get_ffmpeg_options,
    invalidate_cached_stream,
    is_stream_url_expired,
    load_all_guild_settings,
    logger,
    run_media,
    stream_cache_key,
)


class BufferedAudioSource(discord.AudioSource):
    def __init__(
        self,
        source: discord.AudioSource,
        prefetch_frames: int = 50,
        max_frames: int = 500,
        max_silence_frames: int = 300,
    ):
        self.source = source
        self.buffer: "queue.Queue[bytes]" = queue.Queue(maxsize=max_frames)
        self.prefetch_frames = prefetch_frames
        self.max_silence_frames = max_silence_frames
        self._consecutive_silence = 0
        self._stop_event = threading.Event()
        self._finished = False
        self._started = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            logger.debug("Ignored exception in __del__", exc_info=True)

    def _put_frame(self, data: bytes) -> None:
        while not self._stop_event.is_set():
            try:
                self.buffer.put(data, timeout=0.1)
                return
            except queue.Full:
                continue

    def _worker(self) -> None:
        try:
            while not self._stop_event.is_set():
                data = self.source.read()
                if not data:
                    self._finished = True
                    break
                self._put_frame(data)
        except Exception:
            logger.debug("BufferedAudioSource worker stopped", exc_info=True)
            self._finished = True

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self._stop_event.is_set():
            return b''
        try:
            data = self.buffer.get_nowait()
            self._consecutive_silence = 0
            return data
        except queue.Empty:
            if self._finished and self.buffer.empty():
                return b''
            self._consecutive_silence += 1
            if self._consecutive_silence >= self.max_silence_frames:
                logger.warning(
                    "BufferedAudioSource 緩衝區連續飢餓達 %d 幀 (約 %.1f 秒)，判定串流中斷並強制結束",
                    self._consecutive_silence,
                    self._consecutive_silence * 0.02,
                )
                self._finished = True
                return b''
            return bytes(3840)

    def _drain_buffer(self) -> None:
        try:
            while not self.buffer.empty():
                self.buffer.get_nowait()
        except queue.Empty:
            pass
        except Exception:
            logger.debug("Ignored exception in cleanup", exc_info=True)

    def cleanup(self) -> None:
        self._stop_event.set()
        self._drain_buffer()
        if hasattr(self.source, 'cleanup') and callable(self.source.cleanup):
            try:
                self.source.cleanup()
            except Exception:
                logger.debug("Ignored exception in cleanup", exc_info=True)


def _resolve_song_url(raw_str: str, vid_str: str) -> str:
    if raw_str.startswith('http'):
        return raw_str
    if vid_str and re.match(r'^[a-zA-Z0-9_-]{11}$', vid_str):
        return f"https://www.youtube.com/watch?v={vid_str}"
    if raw_str and re.match(r'^[a-zA-Z0-9_-]{11}$', raw_str):
        return f"https://www.youtube.com/watch?v={raw_str}"
    if raw_str and raw_str.startswith(('ytsearch', 'scsearch')):
        return raw_str
    return ''


class Song:
    def __init__(self, data: Optional[dict], requester: Any, wavelink_track: Any = None):
        data = data if isinstance(data, dict) else {}
        self.data = data
        self.title: str = data.get('title', '未知標題') or '未知標題'
        raw_url = data.get('webpage_url') or data.get('url', '') or ''
        vid = data.get('id')
        vid_str = str(vid) if vid is not None else ''
        raw_str = str(raw_url) if raw_url is not None else ''
        self.url: str = _resolve_song_url(raw_str, vid_str)
        stream_u = str(data.get('url', '') or '')
        self.stream_url: str = stream_u if (stream_u.startswith('http') and not ('youtube.com' in stream_u or 'youtu.be' in stream_u)) else ''
        self.duration: int = int(data.get('duration', 0) or 0)
        self.thumbnail: str = str(data.get('thumbnail', '') or '')
        self.uploader: str = str(data.get('uploader', '未知上傳者') or '未知上傳者')
        self.requester: Any = requester
        self.wavelink_track: Any = wavelink_track

    def to_snapshot(self) -> dict:
        return {
            'title': self.title,
            'url': self.url,
            'duration': self.duration,
            'thumbnail': self.thumbnail,
            'uploader': self.uploader,
            'requester_id': getattr(self.requester, 'id', None),
        }

    @classmethod
    def from_snapshot(cls, item: dict, requester: Any) -> 'Song':
        url = item.get('url') or f"ytsearch5:{item.get('title', '')}"
        return cls({
            'title': item.get('title', '未知標題'),
            'webpage_url': url,
            'url': url,
            'duration': item.get('duration', 0),
            'thumbnail': item.get('thumbnail', ''),
            'uploader': item.get('uploader', '未知上傳者'),
        }, requester)


class GuildMusicState:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: deque[Song] = deque()
        self.history: deque[Song] = deque(maxlen=15)
        self.current: Optional[Song] = None
        self.last_played: Optional[Song] = None
        self.loop_mode: str = 'off'
        self.volume: float = 1.0
        self.text_channel: Optional[Any] = None
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
        self.consecutive_failures: int = 0
        self.is_seeking: bool = False
        self.dj_role_id: Optional[int] = None
        self.current_view: Optional[discord.ui.View] = None
        self.progress_task: Optional[asyncio.Task[Any]] = None
        self.preload_task: Optional[asyncio.Task[Any]] = None
        self. _auto_leave_task: Optional[asyncio.Task[Any]] = None
        self.nowplaying_message: Optional[discord.Message] = None
        self.voice_channel: Optional[Any] = None
        self.current_lyrics: Optional[Tuple[str, str, bool]] = None
        self.skip_requested: bool = False
        self.resume_attempts: int = 0
        self.last_active: float = time.time()
        self.load_persisted_settings()

    def cancel_progress_task(self) -> None:
        if self.progress_task and not self.progress_task.done():
            self.progress_task.cancel()
            self.progress_task = None

    def cancel_preload_task(self) -> None:
        if self.preload_task and not self.preload_task.done():
            self.preload_task.cancel()
            self.preload_task = None

    def touch(self) -> None:
        self.last_active = time.time()

    def cleanup(self) -> None:
        self.last_active = time.time()
        self.cancel_progress_task()
        self.cancel_preload_task()
        if self.current_view:
            try:
                self.current_view.stop()
            except Exception:
                logger.debug("Ignored exception in cleanup", exc_info=True)
            self.current_view = None
        self.nowplaying_message = None
        self.voice_channel = None
        self.current_lyrics = None
        self.queue.clear()
        self.current = None
        self.is_playing = False
        self.paused_duration = 0.0
        self.pause_timestamp = 0.0
        self.consecutive_failures = 0
        self.is_seeking = False
        self.skip_requested = False
        self.resume_attempts = 0

    def queue_space(self) -> int:
        """隊列剩餘可加入的歌曲數 (受 MAX_QUEUE_SIZE 限制)。"""
        return max(0, MAX_QUEUE_SIZE - len(self.queue))

    def load_persisted_settings(self) -> None:
        data = load_all_guild_settings().get(str(self.guild_id), {})
        if "volume" in data:
            self.volume = float(data["volume"])
        if "lang" in data:
            self.lang = data["lang"]
        if "mode_247" in data:
            self.mode_247 = bool(data["mode_247"])
        if "bassboost" in data and data["bassboost"] in BASSBOOST_PRESETS:
            self.bassboost = data["bassboost"]
        if "filter" in data and data["filter"] in EQ_PRESETS:
            self.filter = data["filter"]
        if "autoplay" in data:
            self.autoplay = bool(data["autoplay"])
        if "dj_role_id" in data:
            self.dj_role_id = int(data["dj_role_id"]) if data["dj_role_id"] else None


def is_vc_connected(vc: Any) -> bool:
    if not vc or getattr(vc, 'channel', None) is None:
        return False
    if hasattr(vc, 'is_connected') and callable(vc.is_connected):
        return bool(vc.is_connected())
    if hasattr(vc, 'connected'):
        return bool(vc.connected)
    return True


def is_vc_playing(vc: Any) -> bool:
    if not vc:
        return False
    if hasattr(vc, 'playing'):
        return bool(vc.playing)
    if hasattr(vc, 'is_playing') and callable(vc.is_playing):
        return bool(vc.is_playing())
    return False


def is_vc_paused(vc: Any) -> bool:
    if not vc:
        return False
    if hasattr(vc, 'paused'):
        return bool(vc.paused)
    if hasattr(vc, 'is_paused') and callable(vc.is_paused):
        return bool(vc.is_paused())
    return False


def is_vc_active(vc: Any) -> bool:
    return is_vc_playing(vc) or is_vc_paused(vc)


def apply_encoder_bitrate(vc: Any) -> None:
    """依語音頻道位元率調整 Opus 編碼器 (上限 384kbps、下限 64kbps)。"""
    channel = getattr(vc, 'channel', None)
    channel_bitrate = getattr(channel, 'bitrate', None) if channel else None
    if not channel_bitrate:
        return
    target_bps = min(384000, max(64000, channel_bitrate))
    for enc_attr in ('encoder', '_encoder'):
        enc = getattr(vc, enc_attr, None)
        if not enc:
            continue
        try:
            if hasattr(enc, 'set_bitrate') and callable(enc.set_bitrate):
                enc.set_bitrate(target_bps // 1000)
            elif hasattr(enc, 'bitrate'):
                enc.bitrate = target_bps
        except Exception:
            logger.debug("Failed to apply encoder bitrate", exc_info=True)


def make_after_callback(bot: Any, guild: Optional[discord.Guild], state: 'GuildMusicState') -> Any:
    """建立 vc.play(after=...) 回呼：播放結束後排程 play_next。"""
    node_name = getattr(bot, 'node_name', 'Bot')

    def after_callback(error: Any) -> None:
        if state.is_seeking or not guild:
            return
        if error:
            logger.error(f"[{node_name} Player Error] {error}")
        from core.player import play_next

        def _done(fut: Any) -> None:
            try:
                fut.result()
            except Exception as cb_err:
                logger.error(f"[{node_name} AfterCallback Exception] {cb_err}")

        asyncio.run_coroutine_threadsafe(play_next(bot, guild), bot.loop).add_done_callback(_done)

    return after_callback


async def _toggle_wavelink_pause(vc: Any, state: Optional['GuildMusicState']) -> str:
    if vc.paused:
        if asyncio.iscoroutinefunction(vc.pause):
            await vc.pause(False)
        else:
            vc.pause(False)
        if state and state.pause_timestamp > 0:
            state.paused_duration += time.time() - state.pause_timestamp
            state.pause_timestamp = 0.0
        return "resumed"
    if asyncio.iscoroutinefunction(vc.pause):
        await vc.pause(True)
    else:
        vc.pause(True)
    if state:
        state.pause_timestamp = time.time()
    return "paused"


async def _reanchor_paused_stream(vc: Any, state: 'GuildMusicState', bot: Any, guild: discord.Guild) -> bool:
    current_song = state.current
    if not current_song:
        return False
    buffered_source: Optional[BufferedAudioSource] = None
    try:
        state.is_seeking = True
        current_sec = get_playback_elapsed(state)
        invalidate_cached_stream(stream_cache_key(current_song.url, current_song.title))
        from platforms.youtube import extract_stream_url_sync
        song_url, song_title = current_song.url, current_song.title
        fresh_url, _, _ = await run_media(lambda u=song_url, t=song_title: extract_stream_url_sync(u, t))
        stream_url = fresh_url or getattr(current_song, 'stream_url', '')
        if not stream_url:
            state.is_seeking = False
            return False
        current_song.stream_url = stream_url
        await safe_stop(vc)
        if not is_vc_connected(vc):
            target_ch = getattr(vc, 'channel', None)
            if target_ch:
                vc = await safe_voice_connect(bot, target_ch)
        ffmpeg_opts = get_ffmpeg_options(state, seek_seconds=current_sec)
        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
        buffered_source = BufferedAudioSource(source, prefetch_frames=50, max_frames=500)
        audio_source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume)
        apply_encoder_bitrate(vc)
        vc.play(audio_source, after=make_after_callback(bot, guild, state))
        state.start_time = time.time() - current_sec
        state.paused_duration = 0.0
        state.pause_timestamp = 0.0
        state.is_seeking = False
        return True
    except Exception as e:
        logger.error(f"[Resume Reanchor Error] {e}")
        state.is_seeking = False
        if buffered_source:
            buffered_source.cleanup()
        return False


async def safe_pause_resume(vc: Any, state: Optional['GuildMusicState'] = None, bot: Optional[Any] = None, guild: Optional[discord.Guild] = None) -> str:
    if not vc:
        return "none"
    bot = bot or getattr(vc, 'client', None)
    guild = guild or getattr(vc, 'guild', None)
    if hasattr(vc, 'paused'):
        return await _toggle_wavelink_pause(vc, state)

    if is_vc_paused(vc) or (state and state.pause_timestamp > 0):
        pause_dur = (time.time() - state.pause_timestamp) if (state and state.pause_timestamp > 0) else 0.0
        if (
            state
            and state.current
            and bot
            and guild
            and (pause_dur >= 10 or not is_vc_connected(vc))
            and await _reanchor_paused_stream(vc, state, bot, guild)
        ):
            return "resumed"
        if hasattr(vc, 'resume') and callable(vc.resume):
            vc.resume()
            if state and state.pause_timestamp > 0:
                state.paused_duration += time.time() - state.pause_timestamp
                state.pause_timestamp = 0.0
            return "resumed"
    elif is_vc_playing(vc) and hasattr(vc, 'pause') and callable(vc.pause):
        vc.pause()
        if state:
            state.pause_timestamp = time.time()
        return "paused"
    return "none"


async def safe_stop(vc: Any) -> None:
    if not vc:
        return
    try:
        if hasattr(vc, 'stop'):
            if asyncio.iscoroutinefunction(vc.stop):
                await vc.stop()
            else:
                vc.stop()
        for _ in range(20):
            player = getattr(vc, '_player', None)
            if not player or not getattr(player, 'is_alive', lambda: False)():
                break
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"[Safe Stop Error] {e}")


async def _invoke_vc_disconnect(vc: Any) -> None:
    if not hasattr(vc, 'disconnect'):
        return
    try:
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
    except Exception:
        logger.debug("Ignored exception in safe_disconnect", exc_info=True)


def _prune_client_voice_tracking(vc: Any) -> None:
    try:
        client = getattr(vc, 'client', None)
        guild = getattr(vc, 'guild', None)
        if client and guild:
            conn = getattr(client, '_connection', None)
            if conn and hasattr(conn, '_voice_clients') and isinstance(conn._voice_clients, dict):
                conn._voice_clients.pop(guild.id, None)
            vcs = getattr(client, 'voice_clients', None)
            if isinstance(vcs, list) and vc in vcs:
                vcs.remove(vc)
    except Exception:
        logger.debug("Ignored exception in _prune_client_voice_tracking", exc_info=True)


async def safe_disconnect(vc: Any) -> None:
    if not vc:
        return
    try:
        await safe_stop(vc)
    except Exception:
        logger.debug("Ignored exception in safe_disconnect", exc_info=True)
    await _invoke_vc_disconnect(vc)
    if hasattr(vc, 'cleanup') and callable(vc.cleanup):
        try:
            vc.cleanup()
        except Exception:
            logger.debug("Ignored exception in safe_disconnect", exc_info=True)
    _prune_client_voice_tracking(vc)
    if hasattr(vc, 'channel'):
        try:
            vc.channel = None
        except Exception:
            logger.debug("Ignored exception in safe_disconnect", exc_info=True)


async def _reconnect_after_already_connected(
    bot: Any,
    bot_guild: Any,
    voice_channel: Any,
    guild_id: int,
    conn_kwargs: dict[str, Any],
) -> Any:
    stale_vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(
        getattr(bot, 'voice_clients', []), guild__id=guild_id
    )
    if stale_vc:
        await safe_disconnect(stale_vc)
    conn = getattr(bot, '_connection', None)
    if conn and hasattr(conn, '_voice_clients') and isinstance(conn._voice_clients, dict):
        conn._voice_clients.pop(guild_id, None)
    vc = await voice_channel.connect(**conn_kwargs)
    if hasattr(bot, 'get_state'):
        bot.get_state(guild_id).voice_channel = voice_channel
    return vc


async def safe_voice_connect(bot: Any, voice_channel: Any, timeout: float = 30.0, cls: Any = None) -> Any:
    guild_id = voice_channel.guild.id
    bot_guild = bot.get_guild(guild_id) or voice_channel.guild
    vc: Any = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(
        getattr(bot, 'voice_clients', []), guild__id=guild_id
    )
    if vc and not is_vc_connected(vc):
        await safe_disconnect(vc)
        vc = None
    if vc and is_vc_connected(vc):
        if getattr(vc, 'channel', None) != voice_channel and hasattr(vc, 'move_to'):
            await vc.move_to(voice_channel)
        if hasattr(bot, 'get_state'):
            bot.get_state(guild_id).voice_channel = voice_channel
        return vc
    conn_kwargs: dict[str, Any] = {'timeout': timeout, 'self_deaf': True}
    if cls is not None:
        conn_kwargs['cls'] = cls
    else:
        conn_kwargs['reconnect'] = True
    try:
        vc = await voice_channel.connect(**conn_kwargs)
        if hasattr(bot, 'get_state'):
            bot.get_state(guild_id).voice_channel = voice_channel
        return vc
    except Exception as e:
        err_msg = str(e).lower()
        if "already connected" in err_msg or "already in" in err_msg:
            return await _reconnect_after_already_connected(
                bot, bot_guild, voice_channel, guild_id, conn_kwargs
            )
        raise


async def hot_swap_current_stream(bot: Any, guild: Optional[discord.Guild], vc: Any, state: GuildMusicState) -> bool:
    current_song = state.current
    if not vc or not guild or not current_song or not (is_vc_playing(vc) or is_vc_paused(vc)):
        return False
    buffered_source: Optional[BufferedAudioSource] = None
    try:
        state.is_seeking = True
        current_sec = get_playback_elapsed(state)
        was_paused = is_vc_paused(vc) or (state.pause_timestamp > 0)
        stream_url = getattr(current_song, 'stream_url', '')
        if not stream_url or is_stream_url_expired(stream_url):
            from platforms.youtube import extract_stream_url_sync
            song_url, song_title = current_song.url, current_song.title
            fresh_url, _, _ = await run_media(lambda u=song_url, t=song_title: extract_stream_url_sync(u, t))
            stream_url = fresh_url or stream_url
        if not stream_url:
            state.is_seeking = False
            return False
        current_song.stream_url = stream_url
        await safe_stop(vc)
        ffmpeg_opts = get_ffmpeg_options(state, seek_seconds=current_sec)
        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
        buffered_source = BufferedAudioSource(source, prefetch_frames=50, max_frames=500)
        audio_source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume)

        apply_encoder_bitrate(vc)
        after_callback = make_after_callback(bot, guild, state)
        vc.play(audio_source, after=after_callback)
        now = time.time()
        state.start_time = now - current_sec
        state.paused_duration = 0.0
        if was_paused:
            vc.pause()
            state.pause_timestamp = now
        else:
            state.pause_timestamp = 0.0
        state.is_seeking = False
        state.last_active = now
        if state.nowplaying_message:
            try:
                from ui.embeds import build_nowplaying_embed
                embed = build_nowplaying_embed(bot, current_song, state)
                await state.nowplaying_message.edit(embed=embed)
            except Exception:
                logger.debug("Ignored exception in hot_swap_current_stream", exc_info=True)
        return True
    except Exception as e:
        logger.error(f"[HotSwap Error] {e}")
        state.is_seeking = False
        if buffered_source:
            buffered_source.cleanup()
        return False


def has_dj_permission(interaction: discord.Interaction, state: 'GuildMusicState') -> bool:
    if not state.dj_role_id:
        return True
    perms = getattr(interaction.user, 'guild_permissions', None)
    if perms and perms.administrator:
        return True
    roles = getattr(interaction.user, 'roles', None) or []
    if any( getattr(r, 'id', None) == state.dj_role_id for r in roles ):
        return True
    if state.current and getattr(state.current, 'requester', None) and state.current.requester.id == interaction.user.id:
        return True
    vc = interaction.guild.voice_client if interaction.guild else None
    ch = getattr(vc, 'channel', None) if vc else None
    members = getattr(ch, 'members', None) if ch else None
    if members is not None:
        human_members = [m for m in members if not getattr(m, 'bot', False)]
        if len(human_members) <= 1:
            return True
    return False


def get_playback_elapsed(state: GuildMusicState) -> int:
    if not state.current or state.start_time == 0:
        return 0
    now = time.time()
    base = state.pause_timestamp if state.pause_timestamp > 0 else now
    return max(0, int(base - state.start_time - state.paused_duration))
