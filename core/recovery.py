"""Observe SDK reconnection without treating its temporary leave as a kick."""

import asyncio
from typing import Any

from config import logger
from core.models import get_playback_elapsed, hot_swap_current_stream, is_vc_connected, set_playback_position
from core.voice_status import sync_voice_status
from metrics import inc


def sdk_reconnecting(vc: Any) -> bool:
    runner = getattr(getattr(vc, "_connection", None), "_runner", None)
    return runner is not None and not runner.done()


async def restore_transport(bot: Any, guild: Any, state: Any, vc: Any, position: int, paused: bool) -> bool:
    async with state.playback_lock:
        if not state.current:
            return False
        if hasattr(vc, "filters"):
            await vc.seek(position * 1000)
            await vc.pause(paused)
            set_playback_position(state, position, paused=paused)
            restored = True
        else:
            restored = await hot_swap_current_stream(bot, guild, vc, state, position=position, paused=paused)
        if restored:
            await sync_voice_status(state)
        return restored


async def wait_for_transport(bot: Any, guild: Any, state: Any, vc: Any, previous_channel: Any) -> None:
    song = state.current
    generation = state.playback_generation
    position = get_playback_elapsed(state)
    paused = state.pause_timestamp > 0
    set_playback_position(state, position, paused=True)
    logger.warning("Voice recovery started: node=%s guild=%s channel=%s song=%s position=%s", bot.node_id, guild.id, previous_channel.id, song.title, position)
    inc("voice_reconnects_total")
    restored = False
    try:
        for _ in range(70):
            await asyncio.sleep(0.5)
            if state.current is not song or state.playback_generation != generation:
                return
            if is_vc_connected(vc):
                restored = await restore_transport(bot, guild, state, vc, position, paused)
                break
            if not sdk_reconnecting(vc):
                break
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning("Voice recovery failed: node=%s guild=%s", bot.node_id, guild.id, exc_info=True)
    finally:
        state.reconnecting = False
        state.recovery_task = None
    if restored:
        logger.info("Voice recovery complete: node=%s guild=%s position=%s", bot.node_id, guild.id, position)
        inc("voice_recoveries_total")
    elif state.playback_generation == generation:
        inc("voice_recovery_failures_total")
        await bot._handle_self_disconnected(guild, previous_channel, allow_recovery=False)
