from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import wavelink

from core import audio_controls
from core.models import GuildMusicState, Song, get_playback_elapsed, set_playback_position
from utils import get_ffmpeg_options, get_playback_rate


@pytest.fixture
def audio(monkeypatch):
    monkeypatch.setattr(GuildMusicState, 'load_persisted_settings', lambda self: None)
    state = GuildMusicState(123)
    state.current = Song({'title': 'Song', 'webpage_url': 'https://example.com/song', 'duration': 225}, SimpleNamespace(id=42))
    state.is_playing = True
    state.current.stream_url = 'https://example.com/audio'
    vc = SimpleNamespace(
        source=SimpleNamespace(volume=1.0), is_connected=lambda: True,
        is_playing=lambda: True, is_paused=lambda: state.pause_timestamp > 0,
        channel=SimpleNamespace(id=99),
    )
    guild = SimpleNamespace(voice_client=vc)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild, voice_clients=[])
    save = Mock()
    monkeypatch.setattr(audio_controls, 'save_guild_setting', save)
    return bot, state, vc, save


@pytest.mark.parametrize('percent', [0, 75, 200])
async def test_native_volume_updates_audio_source_and_persisted_setting(audio, percent):
    bot, state, vc, save = audio
    await audio_controls.apply_player_volume(bot, state, vc, percent)
    assert vc.source.volume == percent / 100
    assert state.volume == percent / 100
    save.assert_called_once_with(state.guild_id, 'volume', percent / 100)


@pytest.mark.parametrize('percent', [-1, 201])
async def test_invalid_volume_preserves_audio_and_setting(audio, percent):
    bot, state, vc, save = audio
    with pytest.raises(ValueError):
        await audio_controls.apply_player_volume(bot, state, vc, percent)
    assert state.volume == vc.source.volume == 1.0
    save.assert_not_called()


@pytest.mark.parametrize('target,expected', [(-20, 0), (60, 60), (1000, 224)])
async def test_native_seek_clamps_position_and_preserves_pause(audio, monkeypatch, target, expected):
    bot, state, vc, _ = audio
    state.pause_timestamp = 500
    swap = AsyncMock(return_value=True)
    monkeypatch.setattr(audio_controls, 'hot_swap_current_stream', swap)
    await audio_controls.seek_player(bot, state, vc, target)
    assert swap.await_args.kwargs['position'] == expected
    assert swap.await_args.kwargs['paused'] is True


async def test_live_stream_seek_rejected_before_touching_backend(audio, monkeypatch):
    bot, state, vc, _ = audio
    state.current.duration = 0
    swap = AsyncMock(return_value=True)
    monkeypatch.setattr(audio_controls, 'hot_swap_current_stream', swap)
    with pytest.raises(ValueError):
        await audio_controls.seek_player(bot, state, vc, 10)
    swap.assert_not_awaited()


async def test_failed_seek_reports_backend_failure(audio, monkeypatch):
    bot, state, vc, _ = audio
    monkeypatch.setattr(audio_controls, 'hot_swap_current_stream', AsyncMock(return_value=False))
    with pytest.raises(RuntimeError):
        await audio_controls.seek_player(bot, state, vc, 60)


@pytest.mark.parametrize('speed', [0.4, 2.1, float('inf'), float('nan')])
async def test_invalid_speed_never_reconfigures_audio(audio, monkeypatch, speed):
    bot, state, vc, save = audio
    swap = AsyncMock(return_value=True)
    monkeypatch.setattr(audio_controls, 'hot_swap_current_stream', swap)
    with pytest.raises(ValueError):
        await audio_controls.apply_player_speed(bot, state, vc, speed)
    assert state.playback_speed == 1.0
    swap.assert_not_awaited()
    save.assert_not_called()


@pytest.mark.parametrize('setting,value', [('speed', 1.5), ('eq', 'nightcore')])
async def test_failed_effect_change_rolls_back_ui_state_and_setting(audio, monkeypatch, setting, value):
    bot, state, vc, save = audio
    monkeypatch.setattr(audio_controls, 'hot_swap_current_stream', AsyncMock(return_value=False))
    operation = audio_controls.apply_player_speed if setting == 'speed' else audio_controls.apply_player_eq
    with pytest.raises(RuntimeError):
        await operation(bot, state, vc, value)
    assert state.playback_speed == 1.0 and state.filter == 'reset'
    save.assert_not_called()


async def test_invalid_equalizer_cannot_inject_an_ffmpeg_filter(audio, monkeypatch):
    bot, state, vc, save = audio
    swap = AsyncMock(return_value=True)
    monkeypatch.setattr(audio_controls, 'hot_swap_current_stream', swap)
    with pytest.raises(ValueError):
        await audio_controls.apply_player_eq(bot, state, vc, 'volume=100')
    assert state.filter == 'reset'
    swap.assert_not_awaited()
    save.assert_not_called()


def test_media_clock_tracks_requested_speed_and_freezes_during_pause(audio, monkeypatch):
    _, state, _, _ = audio
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    state.playback_speed = 1.5
    set_playback_position(state, 60)
    now[0] += 10
    assert get_playback_elapsed(state) == 75
    set_playback_position(state, 75, paused=True)
    now[0] += 100
    assert get_playback_elapsed(state) == 75
    set_playback_position(state, 75)
    now[0] += 4
    assert get_playback_elapsed(state) == 81


def test_effect_and_requested_speed_compose_without_losing_seek_position(audio, monkeypatch):
    _, state, _, _ = audio
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    state.playback_speed = 1.2
    state.filter = 'nightcore'
    assert get_playback_rate(state) == pytest.approx(1.5)
    set_playback_position(state, 40)
    now[0] += 10
    assert get_playback_elapsed(state) == 55
    # A settings change cannot retroactively speed up time before re-anchoring.
    state.playback_speed = 2.0
    assert get_playback_elapsed(state) == 55
    set_playback_position(state, 55)
    now[0] += 2
    assert get_playback_elapsed(state) == 60


def test_requested_speed_uses_ffmpeg_tempo_and_keeps_equalizer(audio):
    _, state, _, _ = audio
    state.playback_speed = 1.5
    state.filter = 'nightcore'
    options = get_ffmpeg_options(state, seek_seconds=90)
    assert '-ss 90' in options['before_options']
    assert 'asetrate=48000*1.25' in options['options']
    assert 'atempo=1.5' in options['options']


@pytest.fixture
def lavalink(audio):
    bot, state, _, save = audio
    vc = SimpleNamespace(
        channel=SimpleNamespace(id=99), connected=True, paused=False, playing=True,
        filters=wavelink.Filters(), seek=AsyncMock(), set_volume=AsyncMock(), set_filters=AsyncMock(),
    )
    return bot, state, vc, save


async def test_lavalink_volume_uses_integer_percentage_and_rolls_back_failure(lavalink):
    bot, state, vc, save = lavalink
    await audio_controls.apply_player_volume(bot, state, vc, 75)
    vc.set_volume.assert_awaited_once_with(75)
    assert state.volume == 0.75
    save.assert_called_once_with(123, 'volume', 0.75)
    save.reset_mock()
    vc.set_volume.reset_mock()
    vc.set_volume.side_effect = [OSError('node unavailable'), None]
    with pytest.raises(RuntimeError):
        await audio_controls.apply_player_volume(bot, state, vc, 120)
    assert state.volume == 0.75
    assert [call.args[0] for call in vc.set_volume.await_args_list] == [120, 75]
    save.assert_not_called()


async def test_lavalink_seek_converts_media_seconds_to_ms_and_freezes_paused_clock(lavalink, monkeypatch):
    bot, state, vc, _ = lavalink
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    vc.paused = True
    await audio_controls.seek_player(bot, state, vc, 70)
    vc.seek.assert_awaited_once_with(70000)
    now[0] += 100
    assert get_playback_elapsed(state) == 70
    assert state.pause_timestamp > 0


@pytest.mark.parametrize('preset', ['reset', 'bass', 'rock', 'pop', 'vocal', 'treble', 'nightcore', 'vaporwave', '8d'])
async def test_lavalink_equalizer_builds_real_valid_filters_without_mutating_previous(lavalink, preset):
    bot, state, vc, save = lavalink
    old_filters = vc.filters
    old_payload = deepcopy(old_filters())
    state.playback_speed = 1.5
    await audio_controls.apply_player_eq(bot, state, vc, preset)
    applied = vc.set_filters.await_args.args[0]
    assert isinstance(applied, wavelink.Filters)
    assert applied is not old_filters
    assert old_filters() == old_payload
    payload = applied()
    assert len(payload['equalizer']) == 15
    assert all(-0.25 <= band['gain'] <= 1.0 for band in payload['equalizer'])
    rate = {'nightcore': 1.25, 'vaporwave': 0.85}.get(preset, 1.0)
    assert payload['timescale']['speed'] == pytest.approx(1.5 * rate)
    assert payload['timescale']['pitch'] == pytest.approx(rate)
    if preset == '8d':
        assert payload['rotation'] == {'rotationHz': 0.125}
    assert state.filter == preset
    save.assert_called_once_with(123, 'filter', preset)


async def test_lavalink_speed_failure_restores_filters_setting_and_clock(lavalink, monkeypatch):
    bot, state, vc, save = lavalink
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    set_playback_position(state, 20)
    now[0] += 10
    old_filters = vc.filters
    vc.set_filters.side_effect = [OSError('node unavailable'), None]
    with pytest.raises(RuntimeError):
        await audio_controls.apply_player_speed(bot, state, vc, 1.5)
    assert state.playback_speed == 1.0
    assert vc.set_filters.await_args.args[0] is old_filters
    assert get_playback_elapsed(state) == 30
    now[0] += 5
    assert get_playback_elapsed(state) == 35
    save.assert_not_called()


async def test_lavalink_successful_speed_change_preserves_position_and_new_clock_rate(lavalink, monkeypatch):
    bot, state, vc, _ = lavalink
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    set_playback_position(state, 20)
    now[0] += 10
    await audio_controls.apply_player_speed(bot, state, vc, 2.0)
    assert get_playback_elapsed(state) == 30
    now[0] += 5
    assert get_playback_elapsed(state) == 40
    payload = vc.set_filters.await_args.args[0]()
    assert payload['timescale']['speed'] == 2.0


async def test_native_seek_rebuilds_ffmpeg_at_media_position_and_preserves_pause(audio, monkeypatch):
    bot, state, vc, _ = audio
    now = [1000.0]
    monkeypatch.setattr('core.models.time.time', lambda: now[0])
    state.playback_speed = 1.5
    set_playback_position(state, 50, paused=True)

    class SilentSource(discord.AudioSource):
        def read(self):
            return b''

    create_source = Mock(return_value=SilentSource())
    monkeypatch.setattr('core.models.discord.FFmpegPCMAudio', create_source)
    monkeypatch.setattr('core.models.BufferedAudioSource', lambda source, **kwargs: source)
    monkeypatch.setattr('core.models.apply_encoder_bitrate', lambda voice: None)
    monkeypatch.setattr('core.models.make_after_callback', lambda *args: None)
    stop = AsyncMock()
    monkeypatch.setattr('core.models.safe_stop', stop)
    vc.play = Mock()
    vc.pause = Mock()
    await audio_controls.seek_player(bot, state, vc, 90)
    assert '-ss 90' in create_source.call_args.kwargs['before_options']
    assert 'atempo=1.5' in create_source.call_args.kwargs['options']
    stop.assert_awaited_once_with(vc)
    vc.play.assert_called_once()
    vc.pause.assert_called_once()
    now[0] += 100
    assert get_playback_elapsed(state) == 90
    vc.play.call_args.args[0].cleanup()
