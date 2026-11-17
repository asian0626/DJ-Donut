"""Shared audio controls for the native and Lavalink playback engines."""
import contextlib
import math
from typing import Any

from config import BASSBOOST_PRESETS, EQ_PRESETS, save_guild_setting, wavelink
from core.models import (
    GuildMusicState,
    get_playback_elapsed,
    hot_swap_current_stream,
    is_vc_active,
    is_vc_connected,
    is_vc_paused,
    set_playback_position,
)


# Lavalink's 15 fixed frequency bands approximate the native parametric curves.
_EQ_BANDS = {
    'reset': (0.0,) * 15,
    'bass': (0.30, 0.30, 0.28, 0.23, 0.15, 0.06, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    'rock': (0.20, 0.20, 0.20, 0.18, 0.15, 0.05, -0.05, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.20),
    'pop': (-0.05, -0.05, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.20, 0.15, 0.10, 0.05, 0.0, -0.05, -0.05),
    'vocal': (-0.15, -0.15, -0.15, -0.10, -0.05, 0.0, 0.10, 0.15, 0.20, 0.20, 0.20, 0.18, 0.15, 0.05, -0.05),
    'treble': (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05, 0.10, 0.20, 0.25, 0.25, 0.25),
}


def is_lavalink_player(vc: Any) -> bool:
    return hasattr(vc, 'filters') and callable(getattr(vc, 'set_filters', None))


def build_wavelink_filters(state: GuildMusicState) -> Any:
    """Build fresh filters so failed requests never mutate the player's old filters."""
    if wavelink is None:
        raise RuntimeError('Lavalink is unavailable')
    filters = wavelink.Filters()
    bass_gain = float(BASSBOOST_PRESETS.get(state.bassboost, {}).get('gain', 0.0))
    curve = _EQ_BANDS.get(state.filter, _EQ_BANDS['reset'])
    bass_weights = (1.0, 0.8, 0.5, 0.2)
    bands = [
        {'band': index, 'gain': min(1.0, max(-0.25, gain + bass_gain * (bass_weights[index] if index < 4 else 0.0)))}
        for index, gain in enumerate(curve)
    ]
    filters.equalizer.set(bands=bands)
    effect_rate = {'nightcore': 1.25, 'vaporwave': 0.85}.get(state.filter, 1.0)
    filters.timescale.set(speed=state.playback_speed * effect_rate, pitch=effect_rate, rate=1.0)
    if state.filter == '8d':
        filters.rotation.set(rotation_hz=0.125)
    return filters


def _require_player(state: GuildMusicState, vc: Any) -> None:
    if not state.current or not vc or not is_vc_connected(vc) or not is_vc_active(vc):
        raise RuntimeError('No connected player or current track')


def _paused(state: GuildMusicState, vc: Any) -> bool:
    return state.pause_timestamp > 0 or is_vc_paused(vc)


def _guild(bot: Any, state: GuildMusicState, vc: Any) -> Any:
    return bot.get_guild(state.guild_id) or getattr(vc, 'guild', None)


async def _restart_native(bot: Any, state: GuildMusicState, vc: Any, position: int, paused: bool) -> None:
    changed = await hot_swap_current_stream(bot, _guild(bot, state, vc), vc, state, position=position, paused=paused)
    if not changed:
        raise RuntimeError('Could not restart the native audio stream')


async def seek_player(bot: Any, state: GuildMusicState, vc: Any, target: int) -> None:
    """Seek in media seconds and preserve pause; live/unknown tracks cannot seek."""
    async with state.audio_control_lock:
        _require_player(state, vc)
        if state.current is None or state.current.duration <= 0:
            raise ValueError('Cannot seek a live stream or a track with unknown duration')
        target = max(0, min(int(target), state.current.duration - 1))
        was_paused = _paused(state, vc)
        old_position = get_playback_elapsed(state)
        song = state.current
        try:
            await _seek_backend(bot, state, vc, target, was_paused)
        except Exception as exc:
            if state.current is song:
                with contextlib.suppress(Exception):
                    await _seek_backend(bot, state, vc, old_position, was_paused)
            raise RuntimeError('Could not seek the current track') from exc
        state.touch()


async def _seek_backend(bot: Any, state: GuildMusicState, vc: Any, target: int, paused: bool) -> None:
    if is_lavalink_player(vc):
        song = state.current
        await vc.seek(target * 1000)
        if state.current is not song:
            raise RuntimeError('The current track changed while seeking')
        set_playback_position(state, target, paused=paused)
    else:
        await _restart_native(bot, state, vc, target, paused)


async def _set_volume(vc: Any, volume: float) -> None:
    if is_lavalink_player(vc):
        await vc.set_volume(round(volume * 100))
        return
    source = getattr(vc, 'source', None)
    if source is None or not hasattr(source, 'volume'):
        raise RuntimeError('The current audio source cannot change volume')
    source.volume = volume


async def apply_player_volume(bot: Any, state: GuildMusicState, vc: Any, percent: int) -> None:
    """Apply an absolute volume percentage to either backend before saving it."""
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 200:
        raise ValueError('Volume must be an integer from 0 to 200')
    async with state.audio_control_lock:
        _require_player(state, vc)
        previous = state.volume
        volume = percent / 100.0
        try:
            await _set_volume(vc, volume)
            save_guild_setting(state.guild_id, 'volume', volume)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await _set_volume(vc, previous)
            raise RuntimeError('Could not change the player volume') from exc
        state.volume = volume
        state.touch()


async def _apply_effect_setting(bot: Any, state: GuildMusicState, vc: Any, attribute: str, value: Any) -> None:
    _require_player(state, vc)
    previous = getattr(state, attribute)
    position = get_playback_elapsed(state)
    was_paused = _paused(state, vc)
    old_filters = getattr(vc, 'filters', None)
    song = state.current
    setattr(state, attribute, value)
    try:
        if is_lavalink_player(vc):
            await vc.set_filters(build_wavelink_filters(state))
            if state.current is song:
                set_playback_position(state, position, paused=was_paused)
        else:
            await _restart_native(bot, state, vc, position, was_paused)
        if attribute == 'filter':
            save_guild_setting(state.guild_id, 'filter', value)
    except Exception as exc:
        setattr(state, attribute, previous)
        if state.current is song:
            await _restore_effect(bot, state, vc, position, was_paused, old_filters)
        raise RuntimeError('Could not apply the audio setting') from exc
    state.touch()


async def _restore_effect(bot: Any, state: GuildMusicState, vc: Any, position: int, paused: bool, old_filters: Any) -> None:
    with contextlib.suppress(Exception):
        if is_lavalink_player(vc):
            await vc.set_filters(old_filters)
            set_playback_position(state, position, paused=paused)
        else:
            await _restart_native(bot, state, vc, position, paused)


async def apply_player_eq(bot: Any, state: GuildMusicState, vc: Any, preset: str) -> None:
    if preset not in EQ_PRESETS:
        raise ValueError('Unknown equalizer preset')
    async with state.audio_control_lock:
        await _apply_effect_setting(bot, state, vc, 'filter', preset)


async def apply_player_speed(bot: Any, state: GuildMusicState, vc: Any, speed: float) -> None:
    if isinstance(speed, bool) or not isinstance(speed, (float, int)) or not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ValueError('Playback speed must be from 0.5 to 2.0')
    async with state.audio_control_lock:
        await _apply_effect_setting(bot, state, vc, 'playback_speed', float(speed))
