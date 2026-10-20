import discord
import asyncio
import time
import threading
import queue
import re
import subprocess
from collections import deque
from typing import Optional, Dict, List, Any
from config import (
    load_all_guild_settings,
    save_guild_setting,
    BASSBOOST_PRESETS,
    EQ_PRESETS,
    MEDIA_EXECUTOR,
    get_ffmpeg_options,
    is_stream_url_expired,
    invalidate_cached_stream,
    logger
)

class BufferedAudioSource(discord.AudioSource):
    def __init__(self, source: discord.AudioSource, prefetch_frames: int = 50, max_frames: int = 500):
        self.source = source
        self.buffer = queue.Queue(maxsize=max_frames)
        self.prefetch_frames = prefetch_frames
        self._stop_event = threading.Event()
        self._finished = False
        self._started = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                data = self.source.read()
                if not data:
                    self._finished = True
                    break
                while not self._stop_event.is_set():
                    try:
                        self.buffer.put(data, timeout=0.1)
                        break
                    except queue.Full:
                        continue
            except Exception:
                self._finished = True
                break

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self._stop_event.is_set():
            return b''
        try:
            return self.buffer.get_nowait()
        except queue.Empty:
            if self._finished and self.buffer.empty():
                return b''
            return bytes(3840)

    def cleanup(self):
        self._stop_event.set()
        try:
            while not self.buffer.empty():
                try:
                    self.buffer.get_nowait()
                except queue.Empty:
                    break
        except Exception:
            pass
        if hasattr(self.source, 'cleanup') and callable(self.source.cleanup):
            try:
                self.source.cleanup()
            except Exception:
                pass

class Song:
    def __init__(self, data: Optional[dict], requester: discord.Member, wavelink_track=None):
        data = data if isinstance(data, dict) else {}
        self.data = data
        self.title = data.get('title', '未知標題') or '未知標題'
        raw_url = data.get('webpage_url') or data.get('url', '') or ''
        vid = data.get('id')
        vid_str = str(vid) if vid is not None else ''
        raw_str = str(raw_url) if raw_url is not None else ''
        if raw_str.startswith('http'):
            self.url = raw_str
        elif vid_str and re.match(r'^[a-zA-Z0-9_-]{11}$', vid_str):
            self.url = f"https://www.youtube.com/watch?v={vid_str}"
        elif raw_str and re.match(r'^[a-zA-Z0-9_-]{11}$', raw_str):
            self.url = f"https://www.youtube.com/watch?v={raw_str}"
        elif raw_str and (raw_str.startswith('ytsearch') or raw_str.startswith('scsearch')):
            self.url = raw_str
        else:
            self.url = ''
        stream_u = data.get('url', '') or ''
        self.stream_url = stream_u if (stream_u.startswith('http') and not ('youtube.com' in stream_u or 'youtu.be' in stream_u)) else ''
        self.duration = data.get('duration', 0) or 0
        self.thumbnail = data.get('thumbnail', '') or ''
        self.uploader = data.get('uploader', '未知上傳者') or '未知上傳者'
        self.requester = requester
        self.wavelink_track = wavelink_track

class GuildMusicState:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue = deque()
        self.history = deque(maxlen=50)
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
        self.consecutive_failures: int = 0
        self.is_seeking: bool = False
        self.dj_role_id: Optional[int] = None
        self.current_view: Optional[discord.ui.View] = None
        self.progress_task: Optional[asyncio.Task] = None
        self.nowplaying_message: Optional[discord.Message] = None
        self.last_active: float = time.time()
        self.load_persisted_settings()

    def cancel_progress_task(self):
        if self.progress_task and not self.progress_task.done():
            self.progress_task.cancel()
            self.progress_task = None

    def touch(self):
        self.last_active = time.time()

    def cleanup(self):
        self.last_active = time.time()
        self.cancel_progress_task()
        if self.current_view:
            try:
                self.current_view.stop()
            except Exception:
                pass
            self.current_view = None
        self.nowplaying_message = None
        self.queue.clear()
        self.current = None
        self.is_playing = False
        self.paused_duration = 0.0
        self.pause_timestamp = 0.0
        self.consecutive_failures = 0
        self.is_seeking = False

    def load_persisted_settings(self):
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

async def safe_pause_resume(vc, state: Optional['GuildMusicState'] = None, bot: Optional[Any] = None, guild: Optional[discord.Guild] = None) -> str:
    if not vc:
        return "none"
    bot = bot or getattr(vc, 'client', None)
    guild = guild or getattr(vc, 'guild', None)
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
    if is_vc_paused(vc) or (state and state.pause_timestamp > 0):
        pause_dur = (time.time() - state.pause_timestamp) if (state and state.pause_timestamp > 0) else 0
        current_song = state.current if state else None
        if (pause_dur >= 10 or not is_vc_connected(vc)) and current_song and bot and guild:
            try:
                state.is_seeking = True
                current_sec = get_playback_elapsed(state)
                invalidate_cached_stream(current_song.url)
                invalidate_cached_stream(current_song.title)
                loop = asyncio.get_event_loop()
                from platforms.youtube import extract_stream_url_sync
                fresh_url, _, _ = await loop.run_in_executor(
                    MEDIA_EXECUTOR, lambda: extract_stream_url_sync(current_song.url, current_song.title)
                )
                stream_url = fresh_url or getattr(current_song, 'stream_url', '')
                if stream_url:
                    current_song.stream_url = stream_url
                    await safe_stop(vc)
                    if not is_vc_connected(vc):
                        target_ch = getattr(vc, 'channel', None)
                        if target_ch:
                            vc = await safe_voice_connect(bot, target_ch)
                    ffmpeg_opts = get_ffmpeg_options(state, seek_seconds=current_sec)
                    source = discord.FFmpegPCMAudio(stream_url, stderr=subprocess.DEVNULL, **ffmpeg_opts)
                    buffered_source = BufferedAudioSource(source, prefetch_frames=50, max_frames=500)
                    audio_source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume)

                    if hasattr(vc, 'channel') and vc.channel and hasattr(vc.channel, 'bitrate') and vc.channel.bitrate:
                        target_bps = min(384000, max(64000, vc.channel.bitrate))
                        for enc_attr in ('encoder', '_encoder'):
                            enc = getattr(vc, enc_attr, None)
                            if enc:
                                if hasattr(enc, 'set_bitrate') and callable(enc.set_bitrate):
                                    try:
                                        enc.set_bitrate(target_bps // 1000)
                                    except Exception:
                                        pass
                                elif hasattr(enc, 'bitrate'):
                                    try:
                                        enc.bitrate = target_bps
                                    except Exception:
                                        pass

                    def after_callback(error):
                        if state.is_seeking:
                            return
                        if error:
                            logger.error(f"[{bot.node_name if hasattr(bot, 'node_name') else 'Bot'} Player Error] {error}")
                        from core.player import play_next
                        fut = asyncio.run_coroutine_threadsafe(play_next(bot, guild), bot.loop)
                        def _done(f):
                            try:
                                f.result()
                            except Exception as err:
                                logger.error(f"[Resume AfterCallback Error] {err}")
                        fut.add_done_callback(_done)

                    vc.play(audio_source, after=after_callback)
                    state.start_time = time.time() - current_sec
                    state.paused_duration = 0.0
                    state.pause_timestamp = 0.0
                    state.is_seeking = False
                    return "resumed"
            except Exception as e:
                logger.error(f"[Resume Reanchor Error] {e}")
                state.is_seeking = False
                try:
                    if 'buffered_source' in locals() and buffered_source:
                        buffered_source.cleanup()
                except Exception:
                    pass

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
        for _ in range(20):
            player = getattr(vc, '_player', None)
            if not player or not getattr(player, 'is_alive', lambda: False)():
                break
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"[Safe Stop Error] {e}")

async def safe_disconnect(vc):
    if not vc:
        return
    try:
        await safe_stop(vc)
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
        if hasattr(vc, 'cleanup') and callable(vc.cleanup):
            try:
                vc.cleanup()
            except Exception:
                pass
        client = getattr(vc, 'client', None)
        guild = getattr(vc, 'guild', None)
        if client and guild:
            conn = getattr(client, '_connection', None)
            if conn and hasattr(conn, '_voice_clients') and isinstance(conn._voice_clients, dict):
                conn._voice_clients.pop(guild.id, None)
            vcs = getattr(client, 'voice_clients', None)
            if isinstance(vcs, list) and vc in vcs:
                try:
                    vcs.remove(vc)
                except Exception:
                    pass
        if hasattr(vc, 'channel'):
            vc.channel = None
    except Exception as e:
        logger.error(f"[Safe Disconnect Error] {e}")

async def safe_voice_connect(bot: Any, voice_channel: discord.VoiceChannel, timeout: float = 30.0, cls: Any = None) -> Any:
    guild_id = voice_channel.guild.id
    bot_guild = bot.get_guild(guild_id) or voice_channel.guild
    vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild_id)
    if vc and not is_vc_connected(vc):
        await safe_disconnect(vc)
        vc = None
    if vc and is_vc_connected(vc):
        if getattr(vc, 'channel', None) != voice_channel:
            await vc.move_to(voice_channel)
        return vc
    conn_kwargs = {'timeout': timeout, 'self_deaf': True}
    if cls is not None:
        conn_kwargs['cls'] = cls
    else:
        conn_kwargs['reconnect'] = True
    try:
        vc = await voice_channel.connect(**conn_kwargs)
        return vc
    except Exception as e:
        err_msg = str(e).lower()
        if "already connected" in err_msg or "already in" in err_msg:
            stale_vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild_id)
            if stale_vc:
                await safe_disconnect(stale_vc)
            conn = getattr(bot, '_connection', None)
            if conn and hasattr(conn, '_voice_clients') and isinstance(conn._voice_clients, dict):
                conn._voice_clients.pop(guild_id, None)
            vc = await voice_channel.connect(**conn_kwargs)
            return vc
        raise

def has_dj_permission(interaction: discord.Interaction, state: 'GuildMusicState') -> bool:
    if not state.dj_role_id:
        return True
    if hasattr(interaction.user, 'guild_permissions') and interaction.user.guild_permissions.administrator:
        return True
    if hasattr(interaction.user, 'roles') and any(r.id == state.dj_role_id for r in interaction.user.roles):
        return True
    if state.current and getattr(state.current, 'requester', None) and state.current.requester.id == interaction.user.id:
        return True
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and hasattr(vc, 'channel') and vc.channel:
        human_members = [m for m in vc.channel.members if not m.bot]
        if len(human_members) <= 1:
            return True
    return False

def get_playback_elapsed(state: GuildMusicState) -> int:
    if not state.current or state.start_time == 0:
        return 0
    now = time.time()
    if state.pause_timestamp > 0:
        elapsed = state.pause_timestamp - state.start_time - state.paused_duration
    else:
        elapsed = now - state.start_time - state.paused_duration
    return max(0, int(elapsed))
