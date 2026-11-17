from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from core.models import GuildMusicState, Song
from core.player import _refresh_progress_card
from ui.player_card import make_player_progress
from ui.views import MusicControllerView, refresh_nowplaying_message


@pytest.fixture
def player(monkeypatch):
    monkeypatch.setattr(GuildMusicState, 'load_persisted_settings', lambda self: None)
    state = GuildMusicState(123)
    state.current = Song({
        'title': '就忘了吧 - DJ版',
        'webpage_url': 'https://example.com/song',
        'duration': 225,
        'uploader': '劉大拿',
        'thumbnail': 'https://example.com/cover.jpg',
    }, SimpleNamespace(id=42))
    state.voice_channel = SimpleNamespace(id=99)
    guild = SimpleNamespace(voice_client=SimpleNamespace(source=SimpleNamespace(volume=1.0)))
    bot = SimpleNamespace(get_guild=lambda guild_id: guild, voice_clients=[], node_name='DJ Donut')
    view = MusicControllerView(bot, state, lang='en')
    state.current_view = view
    return bot, state, view


def interaction():
    return SimpleNamespace(
        guild_id=123, locale='en-US',
        user=SimpleNamespace(id=42),
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def controls(view):
    return {item.custom_id: item for item in view.walk_children()
            if isinstance(item, (discord.ui.Button, discord.ui.Select))}


def test_card_serializes_as_one_v2_container_with_working_controls(player):
    _, _, view = player
    assert view.has_components_v2()
    payload = view.to_components()
    assert len(payload) == 1 and payload[0]['type'] == 17
    children = payload[0]['components']
    rows = [item for item in children if item['type'] == 1]
    assert len(rows) == 2
    assert [button['custom_id'] for button in rows[0]['components']] == [
        'music_shuffle_btn', 'music_replay', 'music_play_pause', 'music_skip', 'music_fav_add',
    ]
    menu = rows[1]['components'][0]
    assert menu['type'] == 3 and len(menu['options']) == 8
    assert all(not option['label'].startswith('player_') for option in menu['options'])
    assert children[0]['accessory']['media']['url'] == 'https://example.com/cover.jpg'
    assert '<@42>' in children[-1]['content'] and '<#99>' in children[-1]['content']
    assert MusicControllerView.stop is discord.ui.LayoutView.stop


@pytest.mark.parametrize('elapsed,total,clock', [
    (0, 225, '00:00 / 03:45'),
    (-30, 225, '00:00 / 03:45'),
    (300, 225, '03:45 / 03:45'),
    (3600, 7200, '01:00:00 / 02:00:00'),
])
def test_progress_clock_and_marker_stay_within_track(elapsed, total, clock):
    progress = make_player_progress(elapsed, total, lang='en')
    assert progress.startswith(clock)
    assert progress.count('●') == 1
    if elapsed >= total:
        assert progress.endswith('●`')


def test_live_progress_has_no_invented_duration_or_position():
    progress = make_player_progress(70, 0, lang='en')
    assert 'LIVE' in progress
    assert '●' not in progress and '/' not in progress


def test_untrusted_metadata_and_missing_thumbnail_produce_valid_card(player):
    _, state, view = player
    state.current.title = '*title* @everyone [link]'
    state.current.url = 'javascript:alert(1)'
    state.current.thumbnail = ''
    view._update_button_states()
    heading = view.to_components()[0]['components'][0]
    assert heading['type'] == 10
    assert '\\*title\\*' in heading['content']
    assert '@everyone' not in heading['content']
    assert 'javascript:' not in heading['content']


async def test_pause_button_calls_audio_control_and_renders_resume(player, monkeypatch):
    bot, state, view = player
    event = interaction()

    async def pause(vc, current_state, **kwargs):
        assert current_state is state and kwargs['bot'] is bot
        current_state.pause_timestamp = 10

    pause_mock = AsyncMock(side_effect=pause)
    monkeypatch.setattr('ui.views.is_vc_connected', lambda vc: True)
    monkeypatch.setattr('ui.views.safe_pause_resume', pause_mock)
    await controls(view)['music_play_pause'].callback(event)
    pause_mock.assert_awaited_once()
    event.response.defer.assert_awaited_once()
    kwargs = event.edit_original_response.await_args.kwargs
    assert kwargs['view'] is view and kwargs['embeds'] == []
    assert controls(view)['music_play_pause'].emoji.name == '▶️'
    assert 'Paused' in view.to_components()[0]['components'][0]['components'][0]['content']


async def test_select_menu_routes_loop_action_and_refreshes_card(player):
    _, state, view = player
    event = interaction()
    menu = controls(view)['music_controls']
    menu._values = ['toggle_loop']
    await menu.callback(event)
    assert state.loop_mode == 'single'
    event.response.edit_message.assert_awaited_once()
    assert event.response.edit_message.await_args.kwargs['view'] is view


async def test_pending_control_click_survives_card_rebuild(player):
    _, state, view = player
    event = interaction()
    old_menu = controls(view)['music_controls']
    old_menu._values = ['toggle_loop']
    view._update_button_states()
    # Discord may still dispatch the old component while the HTTP edit is pending.
    assert old_menu.view is None
    await old_menu.callback(event)
    assert state.loop_mode == 'single'
    event.response.edit_message.assert_awaited_once()


async def test_refresh_reuses_view_and_clears_legacy_embed(player):
    bot, state, view = player
    message = SimpleNamespace(edit=AsyncMock())
    state.nowplaying_message = message
    await refresh_nowplaying_message(bot, state)
    await refresh_nowplaying_message(bot, state)
    assert state.current_view is view
    assert message.edit.await_count == 2
    assert message.edit.await_args.kwargs == {'content': None, 'embeds': [], 'view': view}


async def test_progress_refresh_follows_replacement_message(player):
    bot, state, _ = player
    old = SimpleNamespace(edit=AsyncMock())
    new = SimpleNamespace(edit=AsyncMock())
    state.nowplaying_message = old
    assert await _refresh_progress_card(bot, state)
    state.nowplaying_message = new
    assert await _refresh_progress_card(bot, state)
    old.edit.assert_awaited_once()
    new.edit.assert_awaited_once()


async def test_progress_survives_deleted_message_when_nowplaying_replaces_it(player):
    bot, state, _ = player
    replacement = SimpleNamespace(edit=AsyncMock())

    async def deleted(**kwargs):
        state.nowplaying_message = replacement
        raise discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'deleted')

    state.nowplaying_message = SimpleNamespace(edit=AsyncMock(side_effect=deleted))
    assert await _refresh_progress_card(bot, state)
    assert await _refresh_progress_card(bot, state)
    replacement.edit.assert_awaited_once()


async def test_stale_controller_rejects_actions_after_replacement(player):
    bot, state, old_view = player
    state.current_view = MusicControllerView(bot, state)
    event = interaction()
    assert not await old_view.interaction_check(event)
    event.response.send_message.assert_awaited_once()
    assert event.response.send_message.await_args.kwargs['ephemeral']
