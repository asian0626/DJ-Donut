"""Unit tests for DJ-Donut core modules (no Discord connection required)."""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from cache import (
    clear_caches,
    get_cached_autocomplete,
    get_cached_lyrics,
    get_cached_metadata,
    get_cached_stream,
    invalidate_cached_stream,
    is_stream_url_expired,
    set_cached_autocomplete,
    set_cached_lyrics,
    set_cached_metadata,
    set_cached_stream,
    stream_cache_key,
)
from i18n import SUPPORTED_LANGUAGES, TRANSLATIONS, resolve_locale, t
from metrics import create_app, inc, record_extraction, snapshot_counters
from platforms.youtube import _clean_title
from settings import APPLE_MUSIC_URL_REGEX, NETEASE_URL_REGEX, SPOTIFY_URL_REGEX
from storage import Storage
from utils import format_duration, get_ffmpeg_options, parse_time_str
from ytdl import get_ytdl_opts


# ---------- utils ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("45", 45),
        ("01:30", 90),
        ("1:02:03", 3723),
        ("  10 ", 10),
        ("invalid", None),
        ("1:2:3:4", None),
        ("-5", None),
    ],
)
def test_parse_time_str(raw, expected):
    assert parse_time_str(raw) == expected


def test_format_duration():
    assert format_duration(0, "zh-TW") == "直播 / 未知"
    assert format_duration(None, "en") == "Live / Unknown"
    assert format_duration(125) == "02:05"


def test_get_ffmpeg_options():
    state = SimpleNamespace(bassboost="off", filter="reset")
    opts = get_ffmpeg_options(state)
    assert opts["options"] == "-vn"
    assert "-ss" not in opts["before_options"]
    assert "-threads 1" in opts["before_options"]

    state_fx = SimpleNamespace(bassboost="medium", filter="nightcore")
    opts_fx = get_ffmpeg_options(state_fx, seek_seconds=30)
    assert "-ss 30" in opts_fx["before_options"]
    assert "bass=g=8" in opts_fx["options"]
    assert "asetrate=48000*1.25" in opts_fx["options"]


# ---------- URL regexes & title cleaning ----------

def test_platform_url_regexes():
    m = SPOTIFY_URL_REGEX.search("https://open.spotify.com/intl-zh/track/4cOdK2wGLETKBW3PvgPWqT?si=abc")
    assert m and m.groups() == ("track", "4cOdK2wGLETKBW3PvgPWqT")
    assert NETEASE_URL_REGEX.search("https://music.163.com/#/song?id=123456")
    assert APPLE_MUSIC_URL_REGEX.search("https://music.apple.com/tw/album/song-name/12345?i=67890")


def test_clean_title():
    raw = "Jay Chou - Mojito (Official Video) [4K MV] feat. Somebody"
    cleaned = _clean_title(raw)
    assert "Official" not in cleaned
    assert "4K" not in cleaned
    assert "Jay Chou - Mojito" in cleaned


# ---------- security: yt-dlp TLS verification ----------

def test_ytdl_opts_enforces_tls_verification():
    opts = get_ytdl_opts()
    assert not opts.get("nocheckcertificate"), "nocheckcertificate must remain disabled"


# ---------- cache ----------

def test_stream_cache_key_unification():
    assert stream_cache_key("https://youtu.be/abc", "Title") == "https://youtu.be/abc"
    assert stream_cache_key("ytsearch5:foo", "Foo") == "ytsearch5:foo"
    assert stream_cache_key("", "Bar") == "title:bar"


def test_stream_and_metadata_cache_roundtrip():
    clear_caches()
    key = stream_cache_key("https://youtu.be/xyz", "Song")
    FutureExp = int(time.time()) + 3600
    url = f"https://rr1.googlevideo.com/videoplayback?expire={FutureExp}&id=1"
    set_cached_stream(key, url, "https://img/1.jpg", 180)
    assert get_cached_stream(key) == (url, "https://img/1.jpg", 180)

    invalidate_cached_stream(key)
    assert get_cached_stream(key) is None

    set_cached_metadata("sp:1", {"title": "T"})
    assert get_cached_metadata("sp:1") == {"title": "T"}

    set_cached_lyrics("song:1", {"plain": "la la", "lrc": []})
    assert get_cached_lyrics("song:1") == {"plain": "la la", "lrc": []}

    set_cached_autocomplete("jay", ["Jay Chou - Mojito"])
    assert get_cached_autocomplete("jay") == ["Jay Chou - Mojito"]


def test_discord_ui_view_methods_not_shadowed():
    import discord

    from ui.views import MusicControllerView, QueuePaginationView

    assert MusicControllerView.stop is discord.ui.View.stop
    assert QueuePaginationView._refresh is discord.ui.View._refresh


def test_stream_url_expiration_detection():
    past = int(time.time()) - 10
    future = int(time.time()) + 3600
    assert is_stream_url_expired("")
    assert is_stream_url_expired(f"https://example.com/audio?expire={past}")
    assert not is_stream_url_expired(f"https://example.com/audio?expire={future}")


# ---------- SQLite storage & legacy migration ----------

def test_storage_settings_favorites_snapshots_and_migration(tmp_path):
    legacy = tmp_path / "guild_settings.json"
    legacy.write_text(
        json.dumps(
            {
                "1001": {"volume": 0.8, "lang": "ja"},
                "_user_favorites": {"42": [{"title": "Fav1", "url": "https://youtu.be/1"}]},
            }
        ),
        encoding="utf-8",
    )
    db_file = tmp_path / "donut.db"
    store = Storage(str(db_file))
    try:
        # Legacy JSON auto-migrated
        assert store.get_guild_setting(1001, "volume") == 0.8
        assert store.get_guild_setting(1001, "lang") == "ja"
        assert len(store.get_user_favorites(42)) == 1

        # Non-blocking update + flush + reopen persistence
        store.save_guild_setting(1001, "autoplay", True)
        ok, total = store.add_user_favorite(42, {"title": "Fav2", "url": "https://youtu.be/2"})
        assert ok and total == 2
        dup, _ = store.add_user_favorite(42, {"title": "Fav2", "url": "https://youtu.be/2"})
        assert not dup

        store.save_queue_snapshot(1001, 1, {"voice_channel_id": 9, "queue": [{"title": "S"}]})
        store.flush()
    finally:
        store.close()

    reopened = Storage(str(db_file))
    try:
        assert reopened.get_guild_setting(1001, "autoplay") is True
        assert len(reopened.get_user_favorites(42)) == 2
        snaps = reopened.load_queue_snapshots(1)
        assert snaps[1001]["voice_channel_id"] == 9
        reopened.delete_queue_snapshot(1001, 1)
        assert 1001 not in reopened.load_queue_snapshots(1)
        removed = reopened.remove_user_favorite(42, 1)
        assert removed and removed["title"] == "Fav1"
        assert reopened.clear_user_favorites(42) == 1
    finally:
        reopened.close()


# ---------- i18n ----------

def test_all_locales_have_complete_identical_keys():
    locales_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")
    per_lang = {}
    for lang in SUPPORTED_LANGUAGES:
        with open(os.path.join(locales_dir, f"{lang}.json"), encoding="utf-8") as fh:
            per_lang[lang] = set(json.load(fh).keys())
    base = per_lang["zh-TW"]
    assert len(base) >= 80
    for lang, keys in per_lang.items():
        assert keys == base, f"Locale {lang} key mismatch: {keys ^ base}"


def test_translation_lookup_and_formatting():
    assert "500" in t("queue_full", lang="en", max_size=500)
    assert "500" in t("queue_full", lang="zh-TW", max_size=500)
    assert t("nonexistent_key_xyz", lang="en") == "nonexistent_key_xyz"
    assert resolve_locale(preferred_lang="ja") == "ja"
    assert len(TRANSLATIONS) >= 80


# ---------- metrics & health server ----------

async def test_metrics_and_health_endpoints():
    inc("songs_played_total")
    record_extraction(0.25, True)
    assert snapshot_counters()["songs_played_total"] >= 1

    fake_bot = SimpleNamespace(
        node_id=1,
        node_name="音樂 1 號",
        lavalink_connected=False,
        guilds=[1, 2],
        states={1: SimpleNamespace(is_playing=True, queue=[1, 2, 3])},
        is_ready=lambda: True,
    )
    app = create_app(lambda: [fake_bot])
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/healthz")
        assert r.status == 200
        assert (await r.json())["ok"] is True

        r = await client.get("/status")
        body = await r.json()
        assert body["active_rooms"] == 1
        assert body["queued_songs"] == 3

        r = await client.get("/metrics")
        text = await r.text()
        assert "donut_active_rooms 1" in text
        assert 'donut_node_ready{node="1"} 1' in text


# ---------- audio buffer starvation & settings tests ----------

def test_buffered_audio_source_starvation():
    import discord
    from core.models import BufferedAudioSource

    class StalledAudioSource(discord.AudioSource):
        def read(self):
            # Simulates a stalled/hung stream that never finishes or returns data
            time.sleep(10)
            return b''

    dummy = StalledAudioSource()
    buf = BufferedAudioSource(dummy, prefetch_frames=5, max_frames=20, max_silence_frames=5)
    try:
        # Before timeout, should return 3840 bytes of silence for first few frames
        for _ in range(4):
            frame = buf.read()
            assert frame == bytes(3840)
        # 5th frame reaches max_silence_frames (5), should terminate with empty bytes b''
        frame = buf.read()
        assert frame == b''
        assert buf._finished is True
    finally:
        buf.cleanup()


def test_force_lavalink_and_sentry_settings():
    from settings import FORCE_LAVALINK, SENTRY_DSN
    assert isinstance(FORCE_LAVALINK, bool)
    assert isinstance(SENTRY_DSN, str)


def test_sqlite_wal_autocheckpoint(tmp_path):
    db_file = str(tmp_path / "test_wal.db")
    storage = Storage(db_file, debounce=0.1)
    try:
        conn = storage._connect()
        # Verify PRAGMA wal_autocheckpoint is active
        cur = conn.execute("PRAGMA wal_autocheckpoint")
        val = cur.fetchone()[0]
        assert val == 1000
    finally:
        storage.close()


