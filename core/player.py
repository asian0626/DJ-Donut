import discord
import asyncio
import time
from typing import Optional, Any
from config import (
    HAS_WAVELINK,
    BASSBOOST_PRESETS,
    MEDIA_EXECUTOR,
    get_ffmpeg_options,
    is_stream_url_expired,
    invalidate_cached_stream,
    logger
)
from core.models import (
    Song,
    GuildMusicState,
    BufferedAudioSource,
    is_vc_connected,
    is_vc_playing,
    is_vc_paused,
    safe_stop,
    safe_disconnect,
    safe_voice_connect,
    has_dj_permission,
    get_playback_elapsed
)
from platforms.youtube import extract_stream_url_sync, fetch_autoplay_recommendation
from platforms.spotify import SPOTIFY_URL_REGEX
from platforms.netease import NETEASE_URL_REGEX
from platforms.apple_music import APPLE_MUSIC_URL_REGEX
from ui.embeds import build_nowplaying_embed
from ui.views import MusicControllerView
from lyrics import get_lyrics_async
from i18n import t

try:
    import wavelink
except ImportError:
    wavelink = None


async def _progress_updater(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Song, message: discord.Message):
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
                pass
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _preload_and_lyrics_worker(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Song):
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
                    pass

        await asyncio.sleep(2.5)
        if not state.is_playing or state.current != song or not state.queue:
            return
        upcoming = state.queue[0]
        if upcoming and (not getattr(upcoming, 'stream_url', '') or is_stream_url_expired(upcoming.stream_url)):
            loop = asyncio.get_event_loop()
            s_url, s_thumb, s_dur = await loop.run_in_executor(
                MEDIA_EXECUTOR, lambda: extract_stream_url_sync(upcoming.url, upcoming.title)
            )
            if s_url:
                upcoming.stream_url = s_url
                if s_thumb and not upcoming.thumbnail:
                    upcoming.thumbnail = s_thumb
                if s_dur and not upcoming.duration:
                    upcoming.duration = s_dur
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _send_clean_nowplaying(bot: Any, guild: discord.Guild, state: GuildMusicState, song: Song):
    if not state.text_channel:
        return
    if state.current_view:
        try:
            state.current_view.stop()
        except Exception:
            pass
    state.cancel_progress_task()
    state.cancel_preload_task()

    old_msg = state.nowplaying_message
    state.nowplaying_message = None
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            pass

    embed = build_nowplaying_embed(bot, song, state)
    view = MusicControllerView(bot, state)
    state.current_view = view
    try:
        msg = await state.text_channel.send(embed=embed, view=view)
        state.nowplaying_message = msg
        state.progress_task = bot.loop.create_task(_progress_updater(bot, guild, state, song, msg))
        state.preload_task = bot.loop.create_task(_preload_and_lyrics_worker(bot, guild, state, song))
    except Exception:
        pass


async def play_next(bot: Any, guild: discord.Guild):
    state = bot.get_state(guild.id)
    vc = guild.voice_client or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild.id)

    state.last_active = time.time()
    state.cancel_progress_task()
    state.cancel_preload_task()

    interrupted_song: Optional[Song] = None
    interrupted_sec: int = 0
    if state.current and not state.skip_requested:
        elapsed = get_playback_elapsed(state)
        dur = state.current.duration or 0
        if dur > 30 and 5 <= elapsed < (dur - 12) and state.resume_attempts < 2:
            interrupted_song = state.current
            interrupted_sec = elapsed

    state.skip_requested = False

    if not vc or not is_vc_connected(vc):
        target_ch = state.voice_channel or (getattr(vc, 'channel', None) if vc else None)
        if target_ch:
            for _ in range(5):
                await asyncio.sleep(0.5)
                vc = guild.voice_client or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=guild.id)
                if vc and is_vc_connected(vc):
                    break
            if not vc or not is_vc_connected(vc):
                try:
                    vc = await safe_voice_connect(bot, target_ch, timeout=20.0)
                except Exception as reconn_err:
                    logger.warning(f"[{bot.node_name}] 語音自動重連失敗: {reconn_err}")
                    state.is_playing = False
                    return
        else:
            state.is_playing = False
            return

    if not vc or not is_vc_connected(vc):
        state.is_playing = False
        return

    if hasattr(vc, 'channel') and vc.channel:
        state.voice_channel = vc.channel

    if interrupted_song:
        state.resume_attempts += 1
        invalidate_cached_stream(interrupted_song.url)
        invalidate_cached_stream(interrupted_song.title)
        interrupted_song.stream_url = ''
        state.queue.appendleft(interrupted_song)
        seek_offset = interrupted_sec
    else:
        state.resume_attempts = 0
        seek_offset = 0
        if state.current:
            state.last_played = state.current
            state.history.appendleft(state.current)
            if state.loop_mode == 'single':
                state.queue.appendleft(state.current)
            elif state.loop_mode == 'queue':
                state.queue.append(state.current)

    while True:
        if not state.queue:
            if state.autoplay and state.last_played:
                try:
                    history_titles = [s.title for s in state.history if s and s.title]
                    rec_song = await fetch_autoplay_recommendation(state.last_played, bot.loop, history_titles=history_titles)
                    if rec_song:
                        state.queue.append(rec_song)
                        if state.text_channel:
                            try:
                                await state.text_channel.send(t('autoplay_next_song', guild_id=guild.id, title=rec_song.title, url=rec_song.url))
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"[{bot.node_name} Autoplay Error] {e}")

            if not state.queue:
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
                        pass
                return

        next_song = state.queue.popleft()
        if state.current != next_song:
            state.current_lyrics = None
        state.current = next_song
        state.is_playing = True
        state.start_time = time.time() - seek_offset
        state.paused_duration = 0.0
        state.pause_timestamp = 0.0

        if bot.lavalink_connected and HAS_WAVELINK and hasattr(vc, 'play') and hasattr(vc, 'filters'):
            if not next_song.wavelink_track:
                try:
                    search_query = next_song.title
                    tracks = await wavelink.Playable.search(search_query)
                    if not tracks and next_song.url and not (SPOTIFY_URL_REGEX.search(next_song.url) or NETEASE_URL_REGEX.search(next_song.url) or APPLE_MUSIC_URL_REGEX.search(next_song.url)):
                        tracks = await wavelink.Playable.search(next_song.url)
                    if not tracks:
                        tracks = await wavelink.Playable.search(f"ytsearch:{search_query}")
                    if tracks:
                        next_song.wavelink_track = tracks[0]
                except Exception as search_err:
                    logger.warning(f"[{bot.node_name}] Lavalink 搜尋音軌略過 ({search_err})")

            if next_song.wavelink_track:
                try:
                    state.active_engine = "⚡ Lavalink v4 (Opus Passthrough)"
                    filters = vc.filters
                    bb_gain = (BASSBOOST_PRESETS.get(state.bassboost) or {}).get("gain", 0.0)
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

                    await vc.set_filters(filters)
                    await vc.play(next_song.wavelink_track)
                    if hasattr(vc, 'set_volume'):
                        await vc.set_volume(int(state.volume * 100))

                    await _send_clean_nowplaying(bot, guild, state, next_song)
                    return
                except Exception as e:
                    logger.warning(f"[{bot.node_name}] Lavalink 播放異常 ({e})，自動切換至極限原生獨立引擎...")

        if hasattr(vc, 'filters'):
            channel = getattr(vc, 'channel', None) or state.voice_channel
            await safe_disconnect(vc)
            if channel:
                try:
                    vc = await safe_voice_connect(bot, channel, timeout=30.0)
                except Exception as conn_err:
                    logger.error(f"[{bot.node_name}] 切換原生語音客戶端失敗: {conn_err}")
                    state.is_playing = False
                    return
            else:
                state.is_playing = False
                return

        state.active_engine = "🔥 Native Studio Engine (384k VBR)"
        preloaded_url = getattr(next_song, 'stream_url', '')
        if preloaded_url and not is_stream_url_expired(preloaded_url):
            stream_url = preloaded_url
            thumbnail = next_song.thumbnail
            duration = next_song.duration
        else:
            loop = asyncio.get_event_loop()
            stream_url, thumbnail, duration = await loop.run_in_executor(
                MEDIA_EXECUTOR, lambda: extract_stream_url_sync(next_song.url, next_song.title)
            )

        if not stream_url:
            seek_offset = 0
            state.consecutive_failures += 1
            if state.loop_mode == 'single' and state.queue and state.queue[0] == next_song:
                state.queue.popleft()
            logger.warning(f"[{bot.node_name}] 無法提取串流: {next_song.title} (連續失敗: {state.consecutive_failures})，自動嘗試下一首...")
            if state.consecutive_failures >= 5:
                state.is_playing = False
                state.current = None
                state.cancel_progress_task()
                state.cancel_preload_task()
                if state.text_channel:
                    try:
                        await state.text_channel.send(t('stream_consecutive_failed', guild_id=guild.id))
                    except Exception:
                        pass
                return
            if state.text_channel:
                try:
                    await state.text_channel.send(t('stream_failed', guild_id=guild.id, bot_name=bot.node_name, title=next_song.title))
                except Exception:
                    pass
            continue

        state.consecutive_failures = 0

        if thumbnail and not next_song.thumbnail:
            next_song.thumbnail = thumbnail
        if duration and not next_song.duration:
            next_song.duration = duration
        next_song.stream_url = stream_url

        try:
            ffmpeg_opts = get_ffmpeg_options(state, seek_seconds=seek_offset if seek_offset > 0 else None)
            source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
            buffered_source = BufferedAudioSource(source, prefetch_frames=50, max_frames=500)
            audio_source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume)

            def after_callback(error):
                if state.is_seeking:
                    return
                if error:
                    logger.error(f"[{bot.node_name} Player Error] {error}")
                def _done_cb(fut):
                    try:
                        fut.result()
                    except Exception as cb_err:
                        logger.error(f"[{bot.node_name} AfterCallback Exception] {cb_err}")
                fut = asyncio.run_coroutine_threadsafe(play_next(bot, guild), bot.loop)
                fut.add_done_callback(_done_cb)

            if not vc or not is_vc_connected(vc):
                logger.warning(f"[{bot.node_name}] 語音客戶端未連線，無法播放")
                state.is_playing = False
                return

            if hasattr(vc, 'channel') and vc.channel and hasattr(vc.channel, 'bitrate') and vc.channel.bitrate:
                target_bps = min(384000, max(64000, vc.channel.bitrate))
                for enc_attr in ('encoder', '_encoder'):
                    enc = getattr(vc, enc_attr, None)
                    if enc:
                        if hasattr(enc, 'set_bitrate') and callable(enc.set_bitrate):
                            try:
                                enc.set_bitrate(target_bps // 1000)
                            except Exception:
                                pass
                        elif hasattr(enc, 'bitrate'):
                            try:
                                enc.bitrate = target_bps
                            except Exception:
                                pass

            if is_vc_playing(vc) or is_vc_paused(vc):
                state.is_seeking = True
                await safe_stop(vc)
                await asyncio.sleep(0.05)
                state.is_seeking = False

            if hasattr(vc, 'play'):
                if asyncio.iscoroutinefunction(vc.play):
                    logger.warning(f"[{bot.node_name}] 警告: vc 為非同步 Player，無法使用原生音源播放")
                else:
                    vc.play(audio_source, after=after_callback)

            state.start_time = time.time() - seek_offset
            seek_offset = 0
            await _send_clean_nowplaying(bot, guild, state, next_song)
            break

        except Exception as e:
            logger.error(f"[{bot.node_name} Play Error] 播放失敗: {e}")
            state.is_seeking = False
            seek_offset = 0
            try:
                if 'buffered_source' in locals() and buffered_source:
                    buffered_source.cleanup()
            except Exception:
                pass
            if next_song.url:
                invalidate_cached_stream(next_song.url)
            if next_song.title:
                invalidate_cached_stream(next_song.title)
            state.consecutive_failures += 1
            if state.consecutive_failures >= 5:
                state.is_playing = False
                state.current = None
                state.cancel_progress_task()
                state.cancel_preload_task()
                if state.text_channel:
                    try:
                        await state.text_channel.send(t('stream_consecutive_failed', guild_id=guild.id))
                    except Exception:
                        pass
                return
            await asyncio.sleep(0.5)
            continue
