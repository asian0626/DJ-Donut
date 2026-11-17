import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from core.models import GuildMusicState, Song, safe_disconnect, safe_pause_resume
from core.node import MusicNodeBot
from core.player import _ensure_queue_or_finish, _send_clean_nowplaying
from core.voice_status import clear_client_voice_status, clear_voice_status, format_voice_status, sync_voice_status


@pytest.fixture
def room(monkeypatch):
    monkeypatch.setattr(GuildMusicState, 'load_persisted_settings', lambda self: None)
    state = GuildMusicState(123)
    state.current = Song({'title': 'Song A', 'webpage_url': 'https://example.com/song', 'duration': 225}, SimpleNamespace(id=42))
    state.is_playing = True
    channel = Mock(spec=discord.VoiceChannel)
    channel.id = 99
    channel.edit = AsyncMock()
    state.voice_channel = channel
    return state, channel


def test_status_normalizes_whitespace_and_respects_discord_limit(room):
    state, _ = room
    state.current.title = '  A\n\t song   title  '
    assert format_voice_status(state) == '🎵 A song title'
    state.current.title = '長' * 600
    status = format_voice_status(state)
    assert status.startswith('🎵 ')
    assert len(status) == 500
    state.current = None
    assert format_voice_status(state) is None


async def test_duplicate_status_is_skipped_but_pause_and_resume_are_visible(room):
    state, channel = room
    await sync_voice_status(state)
    await sync_voice_status(state)
    channel.edit.assert_awaited_once_with(status='🎵 Song A')
    state.pause_timestamp = 1000
    await sync_voice_status(state)
    channel.edit.assert_awaited_with(status='⏸️ Song A')
    state.pause_timestamp = 0
    await sync_voice_status(state)
    channel.edit.assert_awaited_with(status='🎵 Song A')
    assert channel.edit.await_count == 3


async def test_clear_removes_tracked_status_and_is_idempotent(room):
    state, channel = room
    await sync_voice_status(state)
    await clear_voice_status(state)
    await clear_voice_status(state)
    assert [call.kwargs['status'] for call in channel.edit.await_args_list] == ['🎵 Song A', None]


async def test_moving_rooms_clears_previous_channel_before_setting_new_one(room):
    state, previous = room
    next_channel = Mock(spec=discord.VoiceChannel)
    next_channel.id = 100
    next_channel.edit = AsyncMock()
    edits = []

    async def record_previous(**kwargs):
        edits.append((99, kwargs['status']))

    async def record_next(**kwargs):
        edits.append((100, kwargs['status']))

    previous.edit.side_effect = record_previous
    next_channel.edit.side_effect = record_next
    await sync_voice_status(state)
    state.voice_channel = next_channel
    await sync_voice_status(state)
    assert edits == [(99, '🎵 Song A'), (99, None), (100, '🎵 Song A')]
    await clear_voice_status(state)
    assert edits[-1] == (100, None)


async def test_forbidden_status_update_does_not_abort_playback_and_can_retry(room):
    state, channel = room
    channel.edit.side_effect = [
        discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'missing permission'), None,
    ]
    await sync_voice_status(state)
    assert state.is_playing and state.current.title == 'Song A'
    await sync_voice_status(state)
    assert channel.edit.await_count == 2
    await sync_voice_status(state)
    assert channel.edit.await_count == 2


async def test_stage_channel_status_is_skipped(room):
    state, _ = room
    stage = Mock(spec=discord.StageChannel)
    stage.edit = AsyncMock()
    state.voice_channel = stage
    await sync_voice_status(state)
    stage.edit.assert_not_awaited()


async def test_slow_status_edits_are_serialized_and_latest_title_wins(room):
    state, channel = room
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def slow_edit(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(active, max_active)
        if kwargs['status'] == '🎵 Song A':
            started.set()
            await release.wait()
        active -= 1

    channel.edit.side_effect = slow_edit
    first = asyncio.create_task(sync_voice_status(state))
    await started.wait()
    state.current.title = 'Song B'
    second = asyncio.create_task(sync_voice_status(state))
    release.set()
    await asyncio.gather(first, second)
    assert max_active == 1
    assert channel.edit.await_args.kwargs['status'] == '🎵 Song B'


async def test_clear_waits_for_slow_status_update_so_no_old_title_remains(room):
    state, channel = room
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_edit(**kwargs):
        if kwargs['status']:
            started.set()
            await release.wait()

    channel.edit.side_effect = slow_edit
    first = asyncio.create_task(sync_voice_status(state))
    await started.wait()
    clearing = asyncio.create_task(clear_voice_status(state))
    release.set()
    await asyncio.gather(first, clearing)
    assert channel.edit.await_args.kwargs['status'] is None


async def test_playback_sets_voice_status_even_when_no_text_channel_is_available(room, monkeypatch):
    state, channel = room
    monkeypatch.setattr('core.player._resolve_target_text_channel', lambda guild, current_state: None)
    bot = SimpleNamespace(node_name='DJ Donut')
    guild = SimpleNamespace(name='Test guild')
    await _send_clean_nowplaying(bot, guild, state, state.current)
    channel.edit.assert_awaited_once_with(status='🎵 Song A')


async def test_native_pause_resume_updates_voice_status(room, monkeypatch):
    state, channel = room
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    vc = SimpleNamespace(
        channel=channel, is_connected=lambda: True,
        is_paused=lambda: state.pause_timestamp > 0,
        is_playing=lambda: state.pause_timestamp == 0,
        pause=Mock(), resume=Mock(),
    )
    assert await safe_pause_resume(vc, state) == 'paused'
    channel.edit.assert_awaited_with(status='⏸️ Song A')
    now[0] += 2
    assert await safe_pause_resume(vc, state) == 'resumed'
    channel.edit.assert_awaited_with(status='🎵 Song A')
    vc.pause.assert_called_once()
    vc.resume.assert_called_once()


async def test_safe_disconnect_clears_status_before_disconnecting(room):
    state, channel = room
    await sync_voice_status(state)
    order = []

    async def clear(**kwargs):
        order.append('clear')

    async def disconnect(**kwargs):
        order.append('disconnect')

    channel.edit.side_effect = clear
    vc = SimpleNamespace(
        channel=channel, client=SimpleNamespace(states={123: state}), guild=SimpleNamespace(id=123),
        disconnect=AsyncMock(side_effect=disconnect),
    )
    await safe_disconnect(vc)
    assert order == ['clear', 'disconnect']
    assert vc.channel is None


async def test_old_room_cannot_clear_another_bots_status_on_same_channel(room):
    old_state, channel = room
    replacement_state = GuildMusicState(123)
    replacement_state.current = Song({'title': 'Replacement song'}, SimpleNamespace(id=42))
    replacement_state.is_playing = True
    replacement_state.voice_channel = channel
    await sync_voice_status(old_state)
    await sync_voice_status(replacement_state)
    await clear_voice_status(old_state)
    assert [call.kwargs['status'] for call in channel.edit.await_args_list] == ['🎵 Song A', '🎵 Replacement song']
    await clear_voice_status(replacement_state)
    assert channel.edit.await_args.kwargs['status'] is None


@pytest.mark.parametrize('same_channel', [True, False])
async def test_stale_voice_client_cannot_clear_replacements_tracked_status(room, same_channel):
    state, channel = room
    await sync_voice_status(state)
    previous_channel = channel
    if not same_channel:
        previous_channel = Mock(spec=discord.VoiceChannel)
        previous_channel.id = 100
        previous_channel.edit = AsyncMock()
    replacement_vc = SimpleNamespace(channel=channel)
    guild = SimpleNamespace(id=123, voice_client=replacement_vc)
    previous_vc = SimpleNamespace(channel=previous_channel, guild=guild, client=SimpleNamespace(states={123: state}))
    await clear_client_voice_status(previous_vc)
    channel.edit.assert_awaited_once_with(status='🎵 Song A')


async def test_delayed_voice_client_cleanup_rechecks_replacement_after_waiting(room):
    state, channel = room
    await sync_voice_status(state)
    guild = SimpleNamespace(id=123)
    previous_vc = SimpleNamespace(channel=channel, guild=guild, client=SimpleNamespace(states={123: state}))
    guild.voice_client = previous_vc
    state.current.title = 'Song B'
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_edit(**kwargs):
        if kwargs['status'] == '🎵 Song B':
            started.set()
            await release.wait()

    channel.edit.side_effect = slow_edit
    updating = asyncio.create_task(sync_voice_status(state))
    await started.wait()
    clearing = asyncio.create_task(clear_client_voice_status(previous_vc))
    await asyncio.sleep(0)
    guild.voice_client = SimpleNamespace(channel=channel)
    release.set()
    await asyncio.gather(updating, clearing)
    assert [call.kwargs['status'] for call in channel.edit.await_args_list] == ['🎵 Song A', '🎵 Song B']


async def test_finished_queue_clears_status_while_247_bot_stays_in_voice(room):
    state, channel = room
    state.mode_247 = True
    await sync_voice_status(state)
    bot = SimpleNamespace(node_name='DJ Donut')
    guild = SimpleNamespace(id=123)
    assert not await _ensure_queue_or_finish(bot, guild, state)
    assert state.mode_247 and state.voice_channel is channel
    assert not state.is_playing and state.current is None
    channel.edit.assert_awaited_with(status=None)


async def test_bot_close_clears_owned_channel_status_before_shutdown(room):
    state, channel = room
    await sync_voice_status(state)
    bot = MusicNodeBot(1)
    bot.states[123] = state
    await bot.close()
    assert bot.is_closed()
    channel.edit.assert_awaited_with(status=None)
    # An in-flight next-track callback cannot republish a title during shutdown.
    await sync_voice_status(state)
    assert channel.edit.await_count == 2
