"""Keep the connected voice channel's status in sync with music playback."""
import asyncio
from typing import Any, Callable, Optional
from weakref import WeakValueDictionary

import discord

from config import logger


# Cluster bots share ownership so an old room cannot erase another bot's title.
_CHANNEL_OWNERS: WeakValueDictionary[int, 'VoiceChannelStatus'] = WeakValueDictionary()
_CHANNEL_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def _channel_lock(channel_id: int) -> asyncio.Lock:
    return _CHANNEL_LOCKS.setdefault(channel_id, asyncio.Lock())


def format_voice_status(state: Any) -> Optional[str]:
    song = state.current
    if not song or not state.is_playing:
        return None
    title = ' '.join(str(song.title).split())
    if not title:
        return None
    prefix = '⏸️' if state.pause_timestamp > 0 else '🎵'
    return f'{prefix} {title}'[:500]


class VoiceChannelStatus:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._channel: Optional[discord.VoiceChannel] = None
        self._status: Optional[str] = None
        self._closed = False

    async def _write(self, channel: discord.VoiceChannel, status: Optional[str]) -> bool:
        try:
            await channel.edit(status=status)
        except discord.Forbidden:
            logger.warning('Cannot update voice channel status for %s: missing channel status permission', channel.id)
            return False
        except Exception:
            logger.debug('Could not update voice channel status for %s', channel.id, exc_info=True)
            return False
        return True

    async def _clear_owned(self, guard: Optional[Callable[[], bool]] = None) -> None:
        channel = self._channel
        if channel is not None:
            async with _channel_lock(channel.id):
                if guard is not None and not guard():
                    return
                if _CHANNEL_OWNERS.get(channel.id) is self:
                    await self._write(channel, None)
                    _CHANNEL_OWNERS.pop(channel.id, None)
        self._channel = None
        self._status = None

    async def _set_owned(self, channel: discord.VoiceChannel, status: str) -> None:
        async with _channel_lock(channel.id):
            if await self._write(channel, status):
                _CHANNEL_OWNERS[channel.id] = self
                self._channel = channel
                self._status = status

    async def sync(self, state: Any, channel: Any = None) -> None:
        async with self._lock:
            if self._closed:
                return
            # Read live state after acquiring the lock so queued updates use the latest song.
            target = channel or state.voice_channel
            status = format_voice_status(state)
            if not isinstance(target, discord.VoiceChannel) or status is None:
                await self._clear_owned()
                return
            same_channel = self._channel is not None and self._channel.id == target.id
            if same_channel and self._status == status and _CHANNEL_OWNERS.get(target.id) is self:
                return
            if not same_channel:
                await self._clear_owned()
            await self._set_owned(target, status)

    async def clear(self, expected_channel: Any = None, *, guard: Optional[Callable[[], bool]] = None) -> None:
        async with self._lock:
            if guard is not None and not guard():
                return
            if expected_channel is not None and (self._channel is None or self._channel.id != expected_channel.id):
                return
            await self._clear_owned(guard)


    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self._clear_owned()


async def sync_voice_status(state: Any, channel: Any = None) -> None:
    await state.voice_status.sync(state, channel)


async def clear_voice_status(state: Any, expected_channel: Any = None, *, expected_generation: Optional[int] = None) -> None:
    await state.voice_status.clear(
        expected_channel, guard=lambda: expected_generation is None or state.voice_generation == expected_generation,
    )


async def clear_client_voice_status(vc: Any) -> None:
    client = getattr(vc, 'client', None)
    guild = getattr(vc, 'guild', None)
    channel = getattr(vc, 'channel', None)
    active_client = getattr(guild, 'voice_client', None)
    if channel is None or (active_client is not None and active_client is not vc):
        return
    states = getattr(client, 'states', {})
    state = states.get(getattr(guild, 'id', None))
    if state is not None:
        await state.voice_status.clear(channel, guard=lambda: getattr(guild, 'voice_client', None) is None or getattr(guild, 'voice_client', None) is vc)
