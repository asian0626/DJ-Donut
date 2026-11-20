from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        user=SimpleNamespace(id=42, voice=SimpleNamespace(channel=SimpleNamespace(id=99))),
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
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
    assert menu['type'] == 3
    assert [option['value'] for option in menu['options']] == [
        'volume', 'toggle_loop', 'rewind', 'forward', 'view_queue', 'toggle_autoplay',
        'equalizer', 'speed', 'sing_along', 'view_lyrics', 'stop_btn',
    ]
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


async def test_volume_menu_opens_modal_and_submission_applies_requested_percent(player, monkeypatch):
    bot, state, view = player
    event = interaction()
    await view.on_control(event, 'volume')
    modal = event.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord.ui.Modal)
    modal.volume_input._value = '75'
    backend = AsyncMock()
    monkeypatch.setattr('ui.views.apply_player_volume', backend)
    refresh = AsyncMock()
    monkeypatch.setattr('ui.views.refresh_nowplaying_message', refresh)
    submission = interaction()
    await modal.on_submit(submission)
    backend.assert_awaited_once_with(bot, state, bot.get_guild(123).voice_client, 75)
    refresh.assert_awaited_once_with(bot, state)
    submission.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    submission.followup.send.assert_awaited_once()


@pytest.mark.parametrize('value', ['201', '-1', 'hello', '１００'])
async def test_volume_modal_rejects_invalid_input_without_mutating_audio(player, monkeypatch, value):
    _, state, view = player
    event = interaction()
    await view.on_control(event, 'volume')
    modal = event.response.send_modal.await_args.args[0]
    modal.volume_input._value = value
    backend = AsyncMock()
    monkeypatch.setattr('ui.views.apply_player_volume', backend)
    submission = interaction()
    await modal.on_submit(submission)
    assert state.volume == 1.0
    backend.assert_not_awaited()
    assert submission.response.send_message.await_args.kwargs['ephemeral']


async def test_stale_volume_modal_does_not_change_the_next_song(player, monkeypatch):
    _, state, view = player
    event = interaction()
    await view.on_control(event, 'volume')
    modal = event.response.send_modal.await_args.args[0]
    modal.volume_input._value = '75'
    state.current = Song({'title': 'Next song'}, SimpleNamespace(id=42))
    backend = AsyncMock()
    monkeypatch.setattr('ui.views.apply_player_volume', backend)
    await modal.on_submit(interaction())
    backend.assert_not_awaited()


@pytest.mark.parametrize('action,value,backend_name,expected', [
    ('equalizer', 'nightcore', 'apply_player_eq', 'nightcore'),
    ('speed', '0.75', 'apply_player_speed', 0.75),
])
async def test_setting_panels_are_private_and_selection_reaches_audio_backend(
    player, monkeypatch, action, value, backend_name, expected,
):
    bot, state, view = player
    event = interaction()
    await view.on_control(event, action)
    kwargs = event.response.send_message.await_args.kwargs
    assert kwargs['ephemeral']
    panel = kwargs['view']
    select = panel.children[0]
    assert value in [option.value for option in select.options]
    if action == 'speed':
        assert next(option.label for option in select.options if option.value == '0.75') == '0.75x'
    backend = AsyncMock()
    monkeypatch.setattr('ui.views.' + backend_name, backend)
    monkeypatch.setattr('ui.views.refresh_nowplaying_message', AsyncMock())
    selection = interaction()
    assert await panel.interaction_check(selection)
    select._values = [value]
    await select.callback(selection)
    backend.assert_awaited_once_with(bot, state, bot.get_guild(123).voice_client, expected)


@pytest.mark.parametrize('stale', ['owner', 'song', 'controller'])
async def test_setting_panel_checks_owner_song_and_controller(player, stale):
    bot, state, view = player
    event = interaction()
    await view.on_control(event, 'speed')
    panel = event.response.send_message.await_args.kwargs['view']
    selection = interaction()
    if stale == 'owner':
        selection.user.id = 500
    elif stale == 'song':
        state.current = Song({'title': 'Next song'}, SimpleNamespace(id=42))
    else:
        state.current_view = MusicControllerView(bot, state)
    assert not await panel.interaction_check(selection)
    assert selection.response.send_message.await_args.kwargs['ephemeral']


async def test_singalong_enables_synced_lyrics_and_can_be_disabled(player, monkeypatch):
    bot, state, view = player
    lyrics = ('Song', '[00:01.00]Hello\n[00:02.00]World', True)
    fetch = AsyncMock(return_value=lyrics)
    refresh = AsyncMock()
    monkeypatch.setattr('ui.views.get_lyrics_async', fetch)
    monkeypatch.setattr('ui.views.refresh_nowplaying_message', refresh)
    await view.on_control(interaction(), 'sing_along')
    assert state.sing_along and state.current_lyrics == lyrics
    fetch.assert_awaited_once()
    refresh.assert_awaited_once_with(bot, state)
    await view.on_control(interaction(), 'sing_along')
    assert not state.sing_along
    assert refresh.await_count == 2
    fetch.assert_awaited_once()


async def test_singalong_without_synced_lyrics_stays_off(player, monkeypatch):
    _, state, view = player
    monkeypatch.setattr('ui.views.get_lyrics_async', AsyncMock(return_value=('Song', 'Plain lyrics', False)))
    refresh = AsyncMock()
    monkeypatch.setattr('ui.views.refresh_nowplaying_message', refresh)
    await view.on_control(interaction(), 'sing_along')
    assert not state.sing_along
    refresh.assert_not_awaited()


@pytest.mark.parametrize('action,value,backend_name,message', [
    ('volume', '75', 'apply_player_volume', 'Volume set to 75%.'),
    ('speed', '1.25', 'apply_player_speed', 'Speed set to 1.25x.'),
])
async def test_successful_audio_change_still_reports_success_when_card_refresh_fails(
    player, monkeypatch, action, value, backend_name, message,
):
    _, _, view = player
    backend = AsyncMock()
    monkeypatch.setattr('ui.views.' + backend_name, backend)
    refresh = AsyncMock(side_effect=RuntimeError('message was deleted'))
    monkeypatch.setattr('ui.views.refresh_nowplaying_message', refresh)
    event = interaction()
    await view.apply_setting(event, action, value)
    backend.assert_awaited_once()
    refresh.assert_awaited_once()
    event.followup.send.assert_awaited_once_with(message, ephemeral=True)


async def test_lyrics_fetch_cannot_replace_next_songs_lyrics(player, monkeypatch):
    _, state, view = player
    first_song = state.current
    next_song = Song({'title': 'Next song'}, SimpleNamespace(id=42))
    next_lyrics = ('Next song', '[00:01.00]Next lyrics', True)

    async def fetch(title, artist):
        assert title == first_song.title and artist == first_song.uploader
        state.current = next_song
        state.current_lyrics = next_lyrics
        return ('First song', '[00:01.00]Old lyrics', True)

    monkeypatch.setattr('ui.views.get_lyrics_async', AsyncMock(side_effect=fetch))
    event = interaction()
    await view.view_lyrics(event)
    assert state.current is next_song
    assert state.current_lyrics is next_lyrics
    event.followup.send.assert_awaited_once()
    assert 'embed' not in event.followup.send.await_args.kwargs


async def test_autoplay_toggle_updates_visible_on_off_label(player, monkeypatch):
    _, state, view = player
    save = Mock()
    monkeypatch.setattr('ui.views.save_guild_setting', save)

    def autoplay_label():
        return next(option.label for option in controls(view)['music_controls'].options
                    if option.value == 'toggle_autoplay')

    assert autoplay_label() == 'Autoplay (Off)'
    await view.on_control(interaction(), 'toggle_autoplay')
    assert state.autoplay
    assert autoplay_label() == 'Autoplay (On)'
    save.assert_called_with(state.guild_id, 'autoplay', True)
    await view.on_control(interaction(), 'toggle_autoplay')
    assert not state.autoplay
    assert autoplay_label() == 'Autoplay (Off)'
    save.assert_called_with(state.guild_id, 'autoplay', False)
