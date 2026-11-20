import asyncio
import sqlite3
import subprocess
import sys
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import core.player as player
import platforms.spotify as spotify
from core.dave_patch import apply_dave_fix
from core.models import GuildMusicState, Song, make_after_callback
from core.node import MusicNodeBot
from core.dispatcher import _select_bot_from_candidates
from metrics import create_app
from storage import Storage
from ui.views import MusicControllerView
from ytdl import _run_bounded, run_media, run_metadata


@pytest.fixture
def room(monkeypatch):
    monkeypatch.setattr(GuildMusicState, "load_persisted_settings", lambda self: None)
    state = GuildMusicState(123)
    first = Song({"title": "A", "webpage_url": "https://example.com/a", "duration": 180}, NS(id=42))
    second = Song({"title": "B", "webpage_url": "https://example.com/b", "duration": 180}, NS(id=42))
    vc = NS(channel=NS(id=99), is_playing=lambda: False, is_connected=lambda: True)
    guild = NS(id=123, name="Test", voice_client=vc)
    bot = NS(node_id=1, node_name="Test", get_state=lambda gid: state, get_guild=lambda gid: guild, voice_clients=[])
    return state, first, second, vc, guild, bot


async def test_simultaneous_play_keeps_current_and_audio_consistent(room, monkeypatch):
    state, first, second, _vc, guild, bot = room
    state.queue.extend([first, second])
    started = asyncio.Event()
    release = asyncio.Event()
    starts = []

    async def resolve(song):
        started.set()
        await release.wait()
        return "https://example.com/audio"

    async def start(bot, guild, state, vc, song, offset):
        starts.append((song, state.current))
        vc.is_playing = lambda: True
        return True

    monkeypatch.setattr(player, "_resolve_song_stream", resolve)
    monkeypatch.setattr(player, "_try_play_wavelink", AsyncMock(return_value=False))
    monkeypatch.setattr(player, "_start_native_ffmpeg_playback", start)
    one = asyncio.create_task(player.play_next(bot, guild))
    await started.wait()
    two = asyncio.create_task(player.play_next(bot, guild))
    await asyncio.sleep(0)
    assert not starts
    release.set()
    await asyncio.gather(one, two)
    assert starts == [(first, first)]
    assert list(state.queue) == [second]


async def test_stop_invalidates_pending_song_resolution(room, monkeypatch):
    state, first, _, _vc, guild, bot = room
    state.queue.append(first)
    resolving = asyncio.Event()
    release = asyncio.Event()

    async def resolve(song):
        resolving.set()
        await release.wait()
        return "https://example.com/audio"

    start = AsyncMock(return_value=True)
    monkeypatch.setattr(player, "_resolve_song_stream", resolve)
    monkeypatch.setattr(player, "_try_play_wavelink", AsyncMock(return_value=False))
    monkeypatch.setattr(player, "_start_native_ffmpeg_playback", start)
    task = asyncio.create_task(player.play_next(bot, guild))
    await resolving.wait()
    state.cleanup()
    release.set()
    await task
    start.assert_not_awaited()
    assert state.current is None


def test_old_audio_callback_cannot_advance_new_song(room, monkeypatch):
    state, first, second, _, guild, bot = room
    state.current = first
    callback = make_after_callback(bot, guild, state)
    state.current = second
    submit = Mock()
    monkeypatch.setattr("core.models.asyncio.run_coroutine_threadsafe", submit)
    callback(None)
    submit.assert_not_called()


async def test_six_ffmpeg_failures_trigger_breaker(room, monkeypatch):
    state, first, _, vc, guild, bot = room

    async def fail(bot, guild, state, vc, song, offset):
        return await player._handle_consecutive_failure(bot, guild, state, song)

    monkeypatch.setattr(player, "_try_play_wavelink", AsyncMock(return_value=False))
    monkeypatch.setattr(player, "_resolve_song_stream", AsyncMock(return_value="https://example.com/audio"))
    monkeypatch.setattr(player, "_start_native_ffmpeg_playback", fail)
    monkeypatch.setattr(player.asyncio, "sleep", AsyncMock())
    for _ in range(5):
        _, abort = await player._attempt_next_song(bot, guild, state, vc, first, 0)
    assert abort and state.consecutive_failures == 5 and not state.is_playing


@pytest.mark.parametrize("action", ["play_pause", "replay", "skip", "toggle_loop", "toggle_autoplay", "stop_btn", "shuffle_btn", "fav_play"])
@pytest.mark.parametrize("violation", ["different_room", "missing_dj"])
async def test_mutating_controls_reject_wrong_room_and_missing_dj(room, action, violation, monkeypatch):
    state, first, _, vc, _, bot = room
    state.current = first
    state.voice_channel = vc.channel
    view = MusicControllerView(bot, state)
    state.current_view = view
    actor = NS(id=777, voice=NS(channel=NS(id=99 if violation == "missing_dj" else 100)), roles=[], guild_permissions=NS(administrator=False))
    state.dj_role_id = 999 if violation == "missing_dj" else None
    event = NS(user=actor, guild_id=123, guild=NS(voice_client=vc), locale="en-US", response=NS(send_message=AsyncMock()))
    pause = AsyncMock()
    stop = AsyncMock()
    monkeypatch.setattr("ui.views.safe_pause_resume", pause)
    monkeypatch.setattr("ui.views.safe_stop", stop)
    await getattr(view, action)(event)
    event.response.send_message.assert_awaited_once()
    pause.assert_not_awaited()
    stop.assert_not_awaited()
    assert state.current is first


def test_loading_bot_is_reserved_for_its_voice_room(room):
    state, _, _, _, guild, bot = room
    state.pending_requests = 1
    selected, _ = _select_bot_from_candidates([(bot, guild)], 123, 200)
    assert selected is None
    selected, _ = _select_bot_from_candidates([(bot, guild)], 123, 99)
    assert selected is bot


def test_spotify_collection_metadata_is_not_a_fake_track():
    html = '<meta property="og:title" content="Rock Mix"><meta property="og:description" content="Playlist by Test">'
    assert spotify.parse_spotify_html(html, "playlist", "id") is None
    assert spotify.parse_spotify_html(html, "album", "id") is None
    assert spotify.parse_spotify_html(html, "track", "id")["tracks"][0]["title"] == "Rock Mix"


@pytest.mark.parametrize("kind", ["playlist", "album"])
def test_spotify_follows_all_pages_until_import_cap(kind):
    def track(n):
        raw = {"name": str(n), "artists": [], "duration_ms": 1000, "id": str(n)}
        return {"track": raw} if kind == "playlist" else raw

    page = {"items": [track(n) for n in range(100)], "next": "page2"}
    sp = Mock()
    getattr(sp, kind).return_value = {"name": "Test", "tracks": page}
    sp.next.return_value = {"items": [track(n) for n in range(100, 220)], "next": "page3"}
    result = spotify._resolve_spotify_api_sync(sp, kind, "id")
    assert len(result["tracks"]) == 200
    assert sp.next.call_count == 1


async def test_spotify_timeout_is_bounded_and_negative_cached(monkeypatch):
    spotify._SPOTIFY_FAILURES.clear()
    pending = asyncio.Event()

    async def blocked(url):
        await pending.wait()

    resolver = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(spotify, "_resolve_spotify_uncached", resolver)
    monkeypatch.setattr(spotify, "SPOTIFY_TIMEOUT", 0.01)
    url = "https://open.spotify.com/playlist/timeouttest"
    assert await spotify.resolve_spotify(url) is None
    assert await spotify.resolve_spotify(url) is None
    assert resolver.await_count == 1
    spotify._SPOTIFY_FAILURES.clear()


async def test_api_stage_timeout_still_tries_public_track_metadata(monkeypatch):
    monkeypatch.setattr(spotify, "get_spotify_client", Mock(return_value=Mock()))
    monkeypatch.setattr(spotify, "run_metadata", AsyncMock(side_effect=asyncio.TimeoutError()))
    assert await spotify._try_spotify_api("track", "id") is None


async def test_worker_timeout_keeps_capacity_until_running_work_finishes():
    pool = ThreadPoolExecutor(max_workers=1)
    gate = threading.Event()
    calls = []

    def blocked():
        calls.append(True)
        gate.wait(5)

    try:
        for _ in range(3):
            with pytest.raises(asyncio.TimeoutError):
                await _run_bounded(blocked, pool, 1, 0.02)
        assert len(calls) == 1
        assert pool._work_queue.qsize() == 0
        gate.set()
        await asyncio.sleep(0.03)
        assert await _run_bounded(lambda: 42, pool, 1, 1) == 42
    finally:
        gate.set()
        pool.shutdown(wait=True)


async def test_metadata_cannot_starve_audio_pool(monkeypatch):
    import ytdl

    gate = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(ytdl, "API_EXECUTOR", pool)
    try:
        task = asyncio.create_task(run_metadata(lambda: gate.wait(2), timeout=1))
        assert await run_media(lambda: 42, timeout=1) == 42
        gate.set()
        await task
    finally:
        gate.set()
        pool.shutdown(wait=True)


async def test_sqlite_transient_failure_retries_without_another_setting_change(tmp_path, monkeypatch):
    store = Storage(str(tmp_path / "store.db"), debounce=0.01)
    original = store._write_snapshot_row
    written = threading.Event()
    attempts = []

    def flaky(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("transient")
        original(*args)
        written.set()

    monkeypatch.setattr(store, "_write_snapshot_row", flaky)
    try:
        store.save_guild_setting(123, "volume", 0.5)
        assert await asyncio.to_thread(written.wait, 4)
        assert len(attempts) >= 2
        with sqlite3.connect(store.db_path) as conn:
            assert conn.execute('SELECT value FROM guild_settings WHERE key="volume"').fetchone()[0] == "0.5"
    finally:
        store.close()


async def test_lavalink_diagnostics_use_compatible_properties():
    vc = NS(guild=NS(id=123), connected=True, playing=True, channel=NS(id=99))
    state = NS(voice_channel=None, is_playing=True, current=NS(title="A"))
    app = create_app(lambda: [NS(node_id=1, states={123: state}, voice_clients=[vc])])
    handler = next(route.handler for route in app.router.routes() if route.method == "GET" and route.resource.canonical == "/diag")
    response = await handler(None)
    assert response.status == 200
    assert b'"vc_playing": true' in response.body


async def test_missing_voice_channel_raises_instead_of_silent_success():
    apply_dave_fix()
    with pytest.raises(discord.ClientException, match="no target channel"):
        await discord.voice_state.VoiceConnectionState._voice_connect(NS(voice_client=NS(channel=None)))


async def test_sdk_reconnect_preserves_queue_and_restores_same_song(room, monkeypatch):
    import core.recovery as recovery

    state, first, second, vc, guild, _ = room
    state.current = first
    state.is_playing = True
    state.queue.append(second)
    state.voice_channel = vc.channel
    vc.is_connected = lambda: False
    vc._connection = NS(_runner=Mock(done=lambda: False))
    bot = MusicNodeBot(1)
    bot.states[123] = state
    bot.get_guild = lambda gid: guild
    real_sleep = asyncio.sleep

    async def reconnect(delay):
        vc.is_connected = lambda: True
        await real_sleep(0)

    restore = AsyncMock(return_value=True)
    monkeypatch.setattr(recovery.asyncio, "sleep", reconnect)
    monkeypatch.setattr(recovery, "restore_transport", restore)
    try:
        await bot._handle_self_disconnected(guild, vc.channel)
        assert state.reconnecting and list(state.queue) == [second]
        await state.recovery_task
        assert state.current is first and list(state.queue) == [second]
        assert not state.reconnecting
        restore.assert_awaited_once()
    finally:
        await bot.close()


def test_start_script_check_uses_existing_supported_venv(tmp_path):
    script = Path(__file__).parents[1] / "start.sh"
    (tmp_path / "start.sh").write_text(script.read_text())
    (tmp_path / ".venv/bin").mkdir(parents=True)
    executable = tmp_path / ".venv/bin/python"
    executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    executable.chmod(0o755)
    # --check imports application modules; put the actual sources on its search path.
    import os

    env = {**os.environ, "PYTHONPATH": str(script.parent)}
    result = subprocess.run(["/bin/bash", str(tmp_path / "start.sh"), "--check"], capture_output=True, text=True, env=env, timeout=10)  # noqa: S603 -- repository script in a temporary test directory
    assert result.returncode == 0, result.stderr
    assert "2.1.0" in result.stdout


async def test_cancelled_command_cannot_enqueue_late_metadata(room):
    from commands.music import _PLAY_REQUEST, _enqueue_or_start_single_song

    state, first, _, vc, guild, bot = room
    event = NS(followup=NS(send=AsyncMock()), guild_id=123, locale="en-US")
    token = _PLAY_REQUEST.set((state, state.playback_generation))
    try:
        state.cleanup()
        await _enqueue_or_start_single_song(event, bot, guild, vc, state, NS(id=99), first, True)
        assert not state.queue and state.current is None
        assert "cancelled" in event.followup.send.await_args.args[0]
    finally:
        _PLAY_REQUEST.reset(token)


def test_ytdlp_collection_title_cannot_become_a_single_track():
    assert spotify._format_ytdl_spotify_result({"title": "Rock Mix"}, "playlist", "https://example.com") is None


async def test_lyrics_fetch_does_not_block_next_track_preload(room, monkeypatch):
    state, first, _, _, guild, bot = room
    lyrics_started = asyncio.Event()
    preloaded = asyncio.Event()
    cancelled = asyncio.Event()

    async def lyrics(*args):
        lyrics_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def preload(*args):
        preloaded.set()

    monkeypatch.setattr(player, "_lyrics_worker", lyrics)
    monkeypatch.setattr(player, "_preload_after_delay", preload)
    task = asyncio.create_task(player._preload_and_lyrics_worker(bot, guild, state, first))
    await asyncio.wait_for(preloaded.wait(), 1)
    assert lyrics_started.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_confirmed_kick_cleans_room_without_attempting_reconnect(room):
    state, first, second, vc, guild, _ = room
    state.current = first
    state.is_playing = True
    state.voice_channel = vc.channel
    state.queue.append(second)
    vc.is_connected = lambda: False
    vc._connection = NS(_runner=Mock(done=lambda: True))
    bot = MusicNodeBot(1)
    bot.states[123] = state
    bot.get_guild = lambda gid: guild
    bot._disconnect_if_current = AsyncMock(return_value=True)
    try:
        await bot._handle_self_disconnected(guild, vc.channel)
        assert state.current is None and not state.queue
        assert state.recovery_task is None
    finally:
        await bot.close()


def test_start_script_rejects_unsupported_environment_without_recreating_it(tmp_path):
    script = Path(__file__).parents[1] / "start.sh"
    target = tmp_path / "start.sh"
    target.write_text(script.read_text())
    (tmp_path / ".venv/bin").mkdir(parents=True)
    executable = tmp_path / ".venv/bin/python"
    executable.write_text("#!/bin/sh\nexit 1\n")
    executable.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(target), "--check"], capture_output=True, text=True, timeout=10)  # noqa: S603 -- local test fixture
    assert result.returncode == 1
    assert "Unsupported .venv Python" in result.stdout
    assert executable.read_text() == "#!/bin/sh\nexit 1\n"


async def test_native_start_resets_failure_count_only_after_audio_frame(room, monkeypatch):
    state, first, _, vc, guild, bot = room
    state.current = first
    state.is_playing = True
    state.consecutive_failures = 3
    event = threading.Event()
    event.set()
    buffered = NS(first_frame=event, _finished=False, cleanup=Mock())
    monkeypatch.setattr(player.discord, "FFmpegPCMAudio", Mock())
    monkeypatch.setattr(player, "BufferedAudioSource", Mock(return_value=buffered))
    monkeypatch.setattr(player.discord, "PCMVolumeTransformer", Mock(return_value=NS()))
    monkeypatch.setattr(player, "apply_encoder_bitrate", Mock())
    monkeypatch.setattr(player, "_send_clean_nowplaying", AsyncMock())
    vc.play = Mock()
    assert await player._start_native_ffmpeg_playback(bot, guild, state, vc, first, 0)
    vc.play.assert_called_once()
    assert state.consecutive_failures == 0


async def test_empty_ffmpeg_output_counts_as_failure_without_starting_audio(room, monkeypatch):
    state, first, _, vc, guild, bot = room
    state.current = first
    state.is_playing = True
    state.consecutive_failures = 3
    buffered = NS(first_frame=threading.Event(), _finished=True, cleanup=Mock())
    monkeypatch.setattr(player.discord, "FFmpegPCMAudio", Mock())
    monkeypatch.setattr(player, "BufferedAudioSource", Mock(return_value=buffered))
    monkeypatch.setattr(player.discord, "PCMVolumeTransformer", Mock(return_value=NS()))
    monkeypatch.setattr(player.asyncio, "sleep", AsyncMock())
    vc.play = Mock()
    assert not await player._start_native_ffmpeg_playback(bot, guild, state, vc, first, 0)
    vc.play.assert_not_called()
    assert state.consecutive_failures == 4


async def test_play_request_during_recovery_does_not_start_a_second_connection(room, monkeypatch):
    from commands.music import _connect_or_move_bot_vc

    state, first, _, vc, guild, bot = room
    state.current = first
    state.reconnecting = True
    connect = AsyncMock()
    monkeypatch.setattr("commands.music.safe_voice_connect", connect)
    event = NS(guild_id=123)
    connected, ok = await _connect_or_move_bot_vc(event, bot, guild, vc.channel, vc.channel, state, True)
    assert ok and connected is vc
    connect.assert_not_awaited()


async def test_recovering_controls_allow_stop_but_not_pause(room):
    state, first, _, vc, _, bot = room
    state.current = first
    state.voice_channel = vc.channel
    state.reconnecting = True
    view = MusicControllerView(bot, state)
    state.current_view = view
    event = NS(user=NS(id=42, voice=NS(channel=vc.channel)), guild_id=123, locale="en-US", response=NS(send_message=AsyncMock()))
    assert not await view._can_change_player(event)
    assert await view._can_change_player(event, allow_reconnecting=True)
