import discord
import asyncio
import time
import subprocess
from typing import Optional, Any
from config import (
    HAS_WAVELINK,
    BASSBOOST_PRESETS,
    MEDIA_EXECUTOR,
    get_ffmpeg_options,
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
    has_dj_permission,
    get_playback_elapsed
)
from platforms.youtube import extract_stream_url_sync, fetch_autoplay_recommendation
from platforms.spotify import SPOTIFY_URL_REGEX
from platforms.netease import NETEASE_URL_REGEX
from platforms.apple_music import APPLE_MUSIC_URL_REGEX
from ui.embeds import build_nowplaying_embed
from ui.views import MusicControllerView
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
            vc = guild.voice_client
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

async def play_next(bot: Any, guild: discord.Guild):
    state = bot.get_state(guild.id)
    vc = guild.voice_client

    state.cancel_progress_task()

    if not vc or not is_vc_connected(vc):
        state.is_playing = False
        return

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
                state.is_playing = False
                state.cancel_progress_task()
                if state.text_channel:
                    try:
                        await state.text_channel.send(t('queue_finished', guild_id=guild.id, bot_name=bot.node_name))
                    except Exception:
                        pass
                return

        next_song = state.queue.popleft()
        state.current = next_song
        state.is_playing = True
        state.start_time = time.time()
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

                    if state.text_channel:
                        if state.current_view:
                            try:
                                state.current_view.stop()
                            except Exception:
                                pass
                        state.cancel_progress_task()
                        embed = build_nowplaying_embed(bot, next_song, state)
                        view = MusicControllerView(bot, state)
                        state.current_view = view
                        try:
                            msg = await state.text_channel.send(embed=embed, view=view)
                            state.nowplaying_message = msg
                            state.progress_task = bot.loop.create_task(_progress_updater(bot, guild, state, next_song, msg))
                        except Exception:
                            pass
                    return
                except Exception as e:
                    logger.warning(f"[{bot.node_name}] Lavalink 播放異常 ({e})，自動切換至極限原生獨立引擎...")

        if hasattr(vc, 'filters'):
            channel = getattr(vc, 'channel', None)
            await safe_disconnect(vc)
            if channel:
                try:
                    vc = await channel.connect(reconnect=True, timeout=30.0, self_deaf=True)
                except Exception as conn_err:
                    logger.error(f"[{bot.node_name}] 切換原生語音客戶端失敗: {conn_err}")
                    state.is_playing = False
                    return
            else:
                state.is_playing = False
                return

        state.active_engine = "🔥 Native Studio Engine (384k VBR)"
        loop = asyncio.get_event_loop()
        stream_url, thumbnail, duration = await loop.run_in_executor(
            MEDIA_EXECUTOR, lambda: extract_stream_url_sync(next_song.url, next_song.title)
        )

        if not stream_url:
            state.consecutive_failures += 1
            logger.warning(f"[{bot.node_name}] 無法提取串流: {next_song.title} (連續失敗: {state.consecutive_failures})，自動嘗試下一首...")
            if state.consecutive_failures >= 5:
                state.is_playing = False
                state.current = None
                state.cancel_progress_task()
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
            ffmpeg_opts = get_ffmpeg_options(state)
            source = discord.FFmpegPCMAudio(stream_url, stderr=subprocess.DEVNULL, **ffmpeg_opts)
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
                await safe_stop(vc)
                await asyncio.sleep(0.05)

            if hasattr(vc, 'play'):
                if asyncio.iscoroutinefunction(vc.play):
                    logger.warning(f"[{bot.node_name}] 警告: vc 為非同步 Player，無法使用原生音源播放")
                else:
                    vc.play(audio_source, after=after_callback)

            if state.text_channel:
                if state.current_view:
                    try:
                        state.current_view.stop()
                    except Exception:
                        pass
                state.cancel_progress_task()
                embed = build_nowplaying_embed(bot, next_song, state)
                view = MusicControllerView(bot, state)
                state.current_view = view
                try:
                    msg = await state.text_channel.send(embed=embed, view=view)
                    state.nowplaying_message = msg
                    state.progress_task = bot.loop.create_task(_progress_updater(bot, guild, state, next_song, msg))
                except Exception:
                    pass
            break

        except Exception as e:
            logger.error(f"[{bot.node_name} Play Error] 播放失敗: {e}")
            if next_song.url:
                invalidate_cached_stream(next_song.url)
            if next_song.title:
                invalidate_cached_stream(next_song.title)
            state.consecutive_failures += 1
            if state.consecutive_failures >= 5:
                state.is_playing = False
                state.current = None
                state.cancel_progress_task()
                if state.text_channel:
                    try:
                        await state.text_channel.send(t('stream_consecutive_failed', guild_id=guild.id))
                    except Exception:
                        pass
                return
            await asyncio.sleep(0.5)
            continue


