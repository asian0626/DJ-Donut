import discord
import asyncio
import time
import threading
import queue
import re
from collections import deque
from typing import Optional, Dict, List, Any
from config import (
    load_all_guild_settings,
    save_guild_setting,
    BASSBOOST_PRESETS,
    EQ_PRESETS,
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
                        self.buffer.put(data, timeout=0.2)
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
        if not self._started:
            self._started = True
            start_wait = time.time()
            while time.time() - start_wait < 2.0:
                if self._stop_event.is_set() or self._finished or self.buffer.qsize() >= self.prefetch_frames:
                    break
                time.sleep(0.02)
        try:
            return self.buffer.get(timeout=0.5)
        except queue.Empty:
            if self._finished and self.buffer.empty():
                return b''
            return bytes(3840)

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

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

    def cleanup(self):
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
        logger.error(f"[Safe Stop Error] {e}")

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
                    await vc.disconnect()
    except Exception as e:
        logger.error(f"[Safe Disconnect Error] {e}")

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
