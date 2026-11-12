import asyncio
import time
from typing import Any, Optional

import discord

from config import (
    APPLE_MUSIC_URL_REGEX,
    BASSBOOST_PRESETS,
    HAS_WAVELINK,
    NETEASE_URL_REGEX,
    SPOTIFY_URL_REGEX,
    get_ffmpeg_options,
    invalidate_cached_stream,
    is_stream_url_expired,
    logger,
    run_media,
    stream_cache_key,
    wavelink,
)
from core.models import (
    BufferedAudioSource,
    GuildMusicState,
    Song,
    apply_encoder_bitrate,
    get_playback_elapsed,
    is_vc_connected,
    is_vc_paused,
    is_vc_playing,
    make_after_callback,
    safe_disconnect,
    safe_stop,
    safe_voice_connect,
)
from i18n import t
from lyrics import get_lyrics_async
from metrics import inc
from platforms.youtube import extract_stream_url_sync, fetch_autoplay_recommendation
from ui.embeds import build_nowplaying_embed
from ui.views import MusicControllerView


async def _progress_updater(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Song, message: discord.Message) -> None:
    try:
        while state.is_playing and state.current == song:
            await asyncio.sleep(15)
            if not state.is_playing or state.current != song:
                break
            vc = guild.voice_client or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild.id)
            if not vc or not is_vc_connected(vc) or is_vc_paused(vc):
                continue
            embed = build_nowplaying_embed(bot, song, state)
            try:
                await message.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                break
            except Exception:
                logger.debug("Ignored exception in _progress_updater", exc_info=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("Ignored exception in _progress_updater", exc_info=True)


async def _preload_upcoming_stream(state: GuildMusicState, song: Song) -> None:
    if not state.is_playing or state.current != song or not state.queue:
        return
    upcoming = state.queue[0]
    if not upcoming or (upcoming.stream_url and not is_stream_url_expired(upcoming.stream_url)):
        return
    u_url, u_title = upcoming.url, upcoming.title
    s_url, s_thumb, s_dur = await run_media(lambda u=u_url, ti=u_title: extract_stream_url_sync(u, ti))
    if s_url:
        upcoming.stream_url = s_url
        if s_thumb and not upcoming.thumbnail:
            upcoming.thumbnail = s_thumb
        if s_dur and not upcoming.duration:
            upcoming.duration = s_dur


async def _preload_and_lyrics_worker(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Song) -> None:
    try:
        await asyncio.sleep(0.8)
        if not state.is_playing or state.current != song:
            return
        lyrics_res = await get_lyrics_async(song.title, song.uploader or "")
        if state.is_playing and state.current == song and lyrics_res:
            state.current_lyrics = lyrics_res
            if lyrics_res[2] and state.nowplaying_message:
                try:
                    embed = build_nowplaying_embed(bot, song, state)
                    await state.nowplaying_message.edit(embed=embed)
                except Exception:
                    logger.debug("Ignored exception in _preload_and_lyrics_worker", exc_info=True)

        await asyncio.sleep(2.5)
        await _preload_upcoming_stream(state, song)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("Ignored exception in _preload_and_lyrics_worker", exc_info=True)


async def _send_clean_nowplaying(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Song) -> None:
    if not state.text_channel and guild:
        state.text_channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
        )
    if not state.text_channel:
        return
    if state.current_view:
        try:
            state.current_view.stop()
        except Exception:
            logger.debug("Ignored exception in _send_clean_nowplaying", exc_info=True)
    state.cancel_progress_task()
    state.cancel_preload_task()

    old_msg = state.nowplaying_message
    state.nowplaying_message = None
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            logger.debug("Ignored exception in _send_clean_nowplaying", exc_info=True)

    embed = build_nowplaying_embed(bot, song, state)
    view = MusicControllerView(bot, state)
    state.current_view = view
    try:
        msg = await state.text_channel.send(embed=embed, view=view)
        state.nowplaying_message = msg
        state.progress_task = bot.loop.create_task(_progress_updater(bot, guild, state, song, msg))
        state.preload_task = bot.loop.create_task(_preload_and_lyrics_worker(bot, guild, state, song))
    except Exception:
        logger.debug("Ignored exception in _send_clean_nowplaying", exc_info=True)


def _failure_backoff(failures: int) -> float:
    """連續失敗的指數退避秒數：0.5, 1, 2, 4, 8 (上限 8 秒)。"""
    return float(min(0.5 * (2 ** max(0, failures - 1)), 8.0))


async def _ensure_connected_vc(bot: Any, guild: discord.Guild, state: GuildMusicState, vc: Any) -> Any:
    if vc and is_vc_connected(vc):
        return vc
    target_ch = state.voice_channel or (getattr(vc, 'channel', None) if vc else None)
    if not target_ch:
        return None
    for _ in range(5):
        await asyncio.sleep(0.5)
        vc = guild.voice_client or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild.id)
        if vc and is_vc_connected(vc):
            return vc
    try:
        return await safe_voice_connect(bot, target_ch, timeout=20.0)
    except Exception as reconn_err:
        logger.warning(f"[{bot.node_name}] 語音自動重連失敗: {reconn_err}")
        return None


def _prepare_queue_for_next(state: GuildMusicState) -> int:
    interrupted_song: Optional[Song] = None
    interrupted_sec: int = 0
    if state.current and not state.skip_requested:
        elapsed = get_playback_elapsed(state)
        dur = state.current.duration or 0
        if dur > 30 and 5 <= elapsed < (dur - 12) and state.resume_attempts < 2:
            interrupted_song = state.current
            interrupted_sec = elapsed

    state.skip_requested = False

    if interrupted_song:
        state.resume_attempts += 1
        invalidate_cached_stream(stream_cache_key(interrupted_song.url, interrupted_song.title))
        interrupted_song.stream_url = ''
        state.queue.appendleft(interrupted_song)
        return interrupted_sec

    state.resume_attempts = 0
    if state.current:
        state.last_played = state.current
        state.history.appendleft(state.current)
        if state.loop_mode == 'single':
            state.queue.appendleft(state.current)
        elif state.loop_mode == 'queue':
            state.queue.append(state.current)
    return 0


async def _ensure_queue_or_finish(bot: Any, guild: discord.Guild, state: GuildMusicState) -> bool:
    if not state.queue and state.autoplay and state.last_played:
        try:
            history_titles = [s.title for s in state.history if s and s.title]
            rec_song = await fetch_autoplay_recommendation(state.last_played, bot.loop, history_titles=history_titles)
            if rec_song and state.queue_space() > 0:
                state.queue.append(rec_song)
                if state.text_channel:
                    try:
                        await state.text_channel.send(t('autoplay_next_song', guild_id=guild.id, title=rec_song.title, url=rec_song.url))
                    except Exception:
                        logger.debug("Ignored exception in _ensure_queue_or_finish", exc_info=True)
        except Exception as e:
            logger.error(f"[{bot.node_name} Autoplay Error] {e}")

    if state.queue:
        return True

    state.current = None
    state.current_lyrics = None
    state.is_playing = False
    state.last_active = time.time()
    state.cancel_progress_task()
    state.cancel_preload_task()
    if state.text_channel:
        try:
            await state.text_channel.send(t('queue_finished', guild_id=guild.id, bot_name=bot.node_name))
        except Exception:
            logger.debug("Ignored exception in _ensure_queue_or_finish", exc_info=True)
    return False


def _is_external_music_url(url: str) -> bool:
    return bool(SPOTIFY_URL_REGEX.search(url) or NETEASE_URL_REGEX.search(url) or APPLE_MUSIC_URL_REGEX.search(url))


async def _resolve_wavelink_track(bot: Any, song: Song) -> None:
    if song.wavelink_track:
        return
    try:
        tracks = await wavelink.Playable.search(song.title)
        if not tracks and song.url and not _is_external_music_url(song.url):
            tracks = await wavelink.Playable.search(song.url)
        if not tracks:
            tracks = await wavelink.Playable.search(f"ytsearch:{song.title}")
        if tracks:
            song.wavelink_track = tracks[0]
    except Exception as search_err:
        logger.warning(f"[{bot.node_name}] Lavalink 搜尋音軌略過 ({search_err})")


def _apply_wavelink_filters(filters: Any, state: GuildMusicState) -> None:
    bb_gain = float((BASSBOOST_PRESETS.get(state.bassboost) or {}).get("gain", 0.0))
    if bb_gain > 0:
        filters.equalizer.set(bands=[
            {"band": 0, "gain": bb_gain},
            {"band": 1, "gain": bb_gain * 0.8},
            {"band": 2, "gain": bb_gain * 0.5},
            {"band": 3, "gain": bb_gain * 0.2}
        ])
    else:
        filters.equalizer.reset()

    if state.filter == "nightcore":
        filters.timescale.set(speed=1.25, pitch=1.25)
    elif state.filter == "vaporwave":
        filters.timescale.set(speed=0.85, pitch=0.8)
    elif state.filter == "8d":
        filters.rotation.set(hertz=0.2)
    elif state.filter == "reset" and bb_gain == 0:
        filters.reset()


async def _try_play_wavelink(bot: Any, guild: discord.Guild, state: GuildMusicState, vc: Any, song: Song) -> bool:
    if not (bot.lavalink_connected and HAS_WAVELINK and hasattr(vc, 'play') and hasattr(vc, 'filters')):
        return False
    await _resolve_wavelink_track(bot, song)
    if not song.wavelink_track:
        return False
    try:
        state.active_engine = "⚡ Lavalink v4 (Opus Passthrough)"
        filters = vc.filters
        _apply_wavelink_filters(filters, state)
        await vc.set_filters(filters)
        await vc.play(song.wavelink_track)
        if hasattr(vc, 'set_volume'):
            await vc.set_volume(int(state.volume * 100))
        logger.info(f"[{bot.node_name}] 🎶 正在播放 (Lavalink): {song.title} (伺服器: {guild.name})")
        await _send_clean_nowplaying(bot, guild, state, song)
        return True
    except Exception as e:
        logger.warning(f"[{bot.node_name}] Lavalink 播放異常 ({e})，自動切換至極限原生獨立引擎...")
        return False


async def _handle_consecutive_failure(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Optional[Song] = None, notify_single: bool = False) -> bool:
    """回傳 True 代表已達連續失敗上限並終止播放。"""
    state.consecutive_failures += 1
    inc('play_failures_total')
    if song and state.loop_mode == 'single' and state.queue and state.queue[0] == song:
        state.queue.popleft()
    if state.consecutive_failures >= 5:
        state.is_playing = False
        state.current = None
        state.cancel_progress_task()
        state.cancel_preload_task()
        if state.text_channel:
            try:
                await state.text_channel.send(t('stream_consecutive_failed', guild_id=guild.id))
            except Exception:
                logger.debug("Ignored exception in _handle_consecutive_failure", exc_info=True)
        return True
    if notify_single and song and state.text_channel:
        try:
            await state.text_channel.send(t('stream_failed', guild_id=guild.id, bot_name=bot.node_name, title=song.title))
        except Exception:
            logger.debug("Ignored exception in _handle_consecutive_failure", exc_info=True)
    await asyncio.sleep(_failure_backoff(state.consecutive_failures))
    return False


async def _start_native_ffmpeg_playback(bot: Any, guild: discord.Guild, state: GuildMusicState, vc: Any, song: Song, seek_offset: int) -> bool:
    buffered_source: Optional[BufferedAudioSource] = None
    try:
        ffmpeg_opts = get_ffmpeg_options(state, seek_seconds=seek_offset if seek_offset > 0 else None)
        source = discord.FFmpegPCMAudio(song.stream_url, **ffmpeg_opts)
        buffered_source = BufferedAudioSource(source, prefetch_frames=50, max_frames=500)
        audio_source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume)

        if not vc or not is_vc_connected(vc):
            logger.warning(f"[{bot.node_name}] 語音客戶端未連線，無法播放")
            buffered_source.cleanup()
            state.is_playing = False
            return True

        apply_encoder_bitrate(vc)

        if is_vc_playing(vc) or is_vc_paused(vc):
            state.is_seeking = True
            await safe_stop(vc)
            await asyncio.sleep(0.05)
            state.is_seeking = False

        if hasattr(vc, 'play'):
            if asyncio.iscoroutinefunction(vc.play):
                logger.warning(f"[{bot.node_name}] 警告: vc 為非同步 Player，無法使用原生音源播放")
            else:
                vc.play(audio_source, after=make_after_callback(bot, guild, state))

        state.start_time = time.time() - seek_offset
        logger.info(f"[{bot.node_name}] 🎶 正在播放: {song.title} (伺服器: {guild.name})")
        await _send_clean_nowplaying(bot, guild, state, song)
        return True
    except Exception as e:
        logger.error(f"[{bot.node_name} Play Error] 播放失敗: {e}")
        state.is_seeking = False
        if buffered_source:
            buffered_source.cleanup()
        invalidate_cached_stream(stream_cache_key(song.url, song.title))
        return await _handle_consecutive_failure(bot, guild, state, song, notify_single=False)


async def _ensure_native_vc(bot: Any, state: GuildMusicState, vc: Any) -> Any:
    if not hasattr(vc, 'filters'):
        return vc
    channel = getattr(vc, 'channel', None) or state.voice_channel
    await safe_disconnect(vc)
    if not channel:
        return None
    try:
        return await safe_voice_connect(bot, channel, timeout=30.0)
    except Exception as conn_err:
        logger.error(f"[{bot.node_name}] 切換原生語音客戶端失敗: {conn_err}")
        return None


async def _resolve_song_stream(song: Song) -> str:
    preloaded_url = getattr(song, 'stream_url', '')
    if preloaded_url and not is_stream_url_expired(preloaded_url):
        return preloaded_url
    s_url, s_title = song.url, song.title
    stream_url, thumbnail, duration = await run_media(lambda u=s_url, ti=s_title: extract_stream_url_sync(u, ti))
    if stream_url:
        if thumbnail and not song.thumbnail:
            song.thumbnail = thumbnail
        if duration and not song.duration:
            song.duration = duration
        song.stream_url = stream_url
    return stream_url or ''


async def _attempt_next_song(
    bot: Any,
    guild: discord.Guild,
    state: GuildMusicState,
    vc: Any,
    next_song: Song,
    seek_offset: int,
) -> tuple[Any, bool]:
    if state.current != next_song:
        state.current_lyrics = None
    state.current = next_song
    inc('songs_played_total')
    state.is_playing = True
    state.start_time = time.time() - seek_offset
    state.paused_duration = 0.0
    state.pause_timestamp = 0.0

    if await _try_play_wavelink(bot, guild, state, vc, next_song):
        return vc, True

    vc = await _ensure_native_vc(bot, state, vc)
    if not vc:
        state.is_playing = False
        return None, True

    state.active_engine = "🔥 Native Studio Engine (384k VBR)"
    stream_url = await _resolve_song_stream(next_song)
    if not stream_url:
        logger.warning(
            f"[{bot.node_name}] 無法提取串流: {next_song.title} (連續失敗: {state.consecutive_failures + 1})，自動嘗試下一首..."
        )
        should_abort = await _handle_consecutive_failure(bot, guild, state, next_song, notify_single=True)
        return vc, should_abort

    state.consecutive_failures = 0
    done = await _start_native_ffmpeg_playback(bot, guild, state, vc, next_song, seek_offset)
    return vc, done


async def play_next(bot: Any, guild: Optional[discord.Guild]) -> None:
    if not guild:
        return
    state = bot.get_state(guild.id)
    raw_vc = guild.voice_client or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild.id)

    state.last_active = time.time()
    state.cancel_progress_task()
    state.cancel_preload_task()

    seek_offset = _prepare_queue_for_next(state)
    vc = await _ensure_connected_vc(bot, guild, state, raw_vc)
    if not vc or not is_vc_connected(vc):
        state.is_playing = False
        return

    if getattr(vc, 'channel', None):
        state.voice_channel = vc.channel

    while await _ensure_queue_or_finish(bot, guild, state):
        next_song = state.queue.popleft()
        vc, finished = await _attempt_next_song(bot, guild, state, vc, next_song, seek_offset)
        seek_offset = 0
        if finished:
            return
