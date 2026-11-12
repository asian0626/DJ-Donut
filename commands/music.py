import re
import time
import urllib.parse
from collections import deque
from typing import Any, Dict, List, Optional, Tuple
import aiohttp
import discord
from discord import app_commands
from config import (
    run_media,
    MAX_QUEUE_SIZE,
    COMMAND_COOLDOWN,
    SPOTIFY_URL_REGEX,
    NETEASE_URL_REGEX,
    APPLE_MUSIC_URL_REGEX,
    HAS_WAVELINK,
    wavelink,
    get_thread_ytdl,
    get_http_session,
    get_user_favorites,
    add_user_favorite,
    remove_user_favorite,
    clear_user_favorites,
    get_cached_autocomplete,
    set_cached_autocomplete,
    logger,
)
from i18n import t, resolve_locale
from core.models import (
    GuildMusicState,
    Song,
    is_vc_connected,
    is_vc_active,
    has_dj_permission,
    safe_voice_connect,
)
from core.node import NODE_BOTS, MusicNodeBot
from core.dispatcher import (
    get_bot_guild,
    find_best_bot_for_channel,
    get_bot_in_user_room,
    get_user_voice_channel,
)
from core.player import play_next, safe_stop, safe_disconnect
from platforms.spotify import resolve_spotify
from platforms.netease import resolve_netease
from platforms.apple_music import resolve_apple_music
from ui.embeds import (
    build_nowplaying_embed,
    build_queue_embed,
    build_song_queued_embed,
    format_duration,
)
from ui.views import (
    MusicControllerView,
    QueuePaginationView,
    SearchSelectView,
)


async def _send_no_bot_available_msg(interaction: discord.Interaction, guild_id: Optional[int]) -> None:
    guild_bots: List[Tuple[MusicNodeBot, discord.Guild]] = []
    for b in NODE_BOTS:
        bg = get_bot_guild(b, guild_id, interaction.guild)
        if bg:
            guild_bots.append((b, bg))

    if len(guild_bots) <= 1:
        cur_b, cur_bg = guild_bots[0] if guild_bots else (NODE_BOTS[0], interaction.guild)
        cur_vc = cur_bg.voice_client if cur_bg else None
        ch_obj = getattr(cur_vc, 'channel', None) if cur_vc else None
        cur_room_name = f"`#{getattr(ch_obj, 'name', '')}`" if ch_obj else "其他房間"
        msg = t('only_one_bot_busy', interaction, bot_name=cur_b.node_name, room_name=cur_room_name)
        await interaction.followup.send(msg)
    else:
        await interaction.followup.send(t('no_bot_available', interaction))


async def _resolve_bot_and_channel_for_play(
    interaction: discord.Interaction,
    voice_channel: Any,
    bot_id: Optional[int],
) -> Optional[Tuple[MusicNodeBot, discord.Guild, Any, GuildMusicState, bool]]:
    guild_id = interaction.guild_id
    selected_bot: Optional[MusicNodeBot] = None
    already_in_room = False

    if bot_id is not None:
        selected_bot = next((b for b in NODE_BOTS if b.node_id == bot_id), None)
        if not selected_bot:
            await interaction.followup.send(
                f"❌ 找不到 【音樂 {bot_id} 號】，目前上線的機器人數量為 {len(NODE_BOTS)} 隻！"
            )
            return None
    else:
        selected_bot, already_in_room = await find_best_bot_for_channel(
            guild_id, voice_channel, interaction.guild
        )

    if not selected_bot:
        await _send_no_bot_available_msg(interaction, guild_id)
        return None

    bot_guild = get_bot_guild(selected_bot, guild_id, interaction.guild) or interaction.guild
    if not bot_guild:
        await interaction.followup.send(t('bot_not_in_guild', interaction, bot_name=selected_bot.node_name))
        return None

    bot_voice_channel: Any = bot_guild.get_channel(voice_channel.id) or voice_channel
    state = selected_bot.get_state(guild_id)
    if state.queue_space() <= 0:
        await interaction.followup.send(t('queue_full', interaction, max_size=MAX_QUEUE_SIZE))
        return None
    if interaction.channel and hasattr(interaction.channel, 'send'):
        state.text_channel = interaction.channel

    try:
        max_bitrate = int(bot_voice_channel.guild.bitrate_limit)
        if getattr(bot_voice_channel, 'bitrate', max_bitrate) < max_bitrate:
            await bot_voice_channel.edit(bitrate=max_bitrate)
    except Exception:
        logger.debug("Ignored exception in slash_play bitrate boost", exc_info=True)

    return selected_bot, bot_guild, bot_voice_channel, state, already_in_room


async def _connect_or_move_bot_vc(
    interaction: discord.Interaction,
    selected_bot: MusicNodeBot,
    bot_guild: discord.Guild,
    bot_voice_channel: Any,
    voice_channel: Any,
    state: GuildMusicState,
    already_in_room: bool,
) -> Tuple[Any, bool]:
    guild_id = interaction.guild_id
    vc: Any = bot_guild.voice_client or discord.utils.get(
        getattr(selected_bot, 'voice_clients', []), guild__id=guild_id
    )
    is_wavelink_player = bool(HAS_WAVELINK and hasattr(vc, 'filters'))
    should_be_wavelink = bool(selected_bot.lavalink_connected and HAS_WAVELINK)

    if vc and is_vc_connected(vc) and (is_wavelink_player != should_be_wavelink):
        await safe_disconnect(vc)
        vc = None

    if not vc or not is_vc_connected(vc):
        try:
            if should_be_wavelink:
                try:
                    vc = await safe_voice_connect(selected_bot, bot_voice_channel, cls=wavelink.Player)
                except Exception as wl_conn_err:
                    logger.warning(
                        f"[{selected_bot.node_name}] Lavalink Player 連線異常 ({wl_conn_err})，使用原生語音連線..."
                    )
                    vc = await safe_voice_connect(selected_bot, bot_voice_channel, timeout=30.0)
            else:
                vc = await safe_voice_connect(selected_bot, bot_voice_channel, timeout=30.0)
        except Exception as e:
            await interaction.followup.send(
                t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e))
            )
            return None, False
    elif getattr(vc, 'channel', None) and vc.channel.id != voice_channel.id:
        if not already_in_room:
            state.loop_mode = 'off'
            state.current = None
            state.queue.clear()
            state.is_playing = False
            await safe_stop(vc)
        await vc.move_to(bot_voice_channel)

    return vc, True


async def _enqueue_or_start_single_song(
    interaction: discord.Interaction,
    selected_bot: MusicNodeBot,
    bot_guild: discord.Guild,
    vc: Any,
    state: GuildMusicState,
    voice_channel: Any,
    song: Song,
    already_in_room: bool,
    require_current_for_queue: bool = True,
) -> None:
    is_busy = is_vc_active(vc) and (state.current is not None if require_current_for_queue else True)
    state.queue.append(song)
    if is_busy:
        embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
        await interaction.followup.send(embed=embed)
    else:
        if not already_in_room:
            await interaction.followup.send(
                t(
                    'song_dispatched',
                    interaction,
                    title=song.title,
                    bot_name=selected_bot.node_name,
                    channel=voice_channel.mention,
                )
            )
        else:
            await interaction.followup.send(
                t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name)
            )
        await play_next(selected_bot, bot_guild)


async def _try_lavalink_direct_play(
    interaction: discord.Interaction,
    selected_bot: MusicNodeBot,
    bot_guild: discord.Guild,
    vc: Any,
    state: GuildMusicState,
    voice_channel: Any,
    query: str,
    already_in_room: bool,
) -> bool:
    is_custom_music_service = bool(
        SPOTIFY_URL_REGEX.search(query)
        or NETEASE_URL_REGEX.search(query)
        or APPLE_MUSIC_URL_REGEX.search(query)
        or ("163cn.tv" in query or "163.lu" in query or "apple.co" in query)
        or ("playlist" in query.lower() or "list=" in query.lower())
    )
    if not (selected_bot.lavalink_connected and HAS_WAVELINK and not is_custom_music_service):
        return False

    try:
        tracks = await wavelink.Playable.search(query)
        if tracks:
            wl_track = tracks[0]
            song = Song(
                {
                    'title': wl_track.title,
                    'url': wl_track.uri or query,
                    'duration': int(wl_track.length // 1000) if hasattr(wl_track, 'length') else 0,
                    'thumbnail': wl_track.artwork or '',
                    'uploader': wl_track.author or '未知',
                },
                interaction.user,
                wavelink_track=wl_track,
            )
            await _enqueue_or_start_single_song(
                interaction,
                selected_bot,
                bot_guild,
                vc,
                state,
                voice_channel,
                song,
                already_in_room,
                require_current_for_queue=False,
            )
            return True
    except Exception as e:
        logger.debug(f"[Slash Play] Lavalink 直通搜尋略過 ({e})，無縫使用原生解析管線...")
    return False


async def _import_external_playlist(
    interaction: discord.Interaction,
    selected_bot: MusicNodeBot,
    bot_guild: discord.Guild,
    vc: Any,
    state: GuildMusicState,
    query: str,
    ext_res: Dict[str, Any],
) -> None:
    platform_name = str(ext_res.get('platform_name', '音樂平台'))
    embed_color = int(ext_res.get('color', 0x1DB954))
    tracks = ext_res.get('tracks', [])
    playlist_title = str(ext_res.get('title') or f"{platform_name} 播放清單")
    playlist_thumb = str(ext_res.get('thumbnail') or '')
    added_count = 0

    for track_item in tracks[: min(200, state.queue_space())]:
        if not track_item or not isinstance(track_item, dict):
            continue
        t_title = str(track_item.get('title') or '未知歌曲')
        t_artist = str(track_item.get('artist') or '')
        title_str = f"{t_title} - {t_artist}" if t_artist else t_title
        search_q = f"ytsearch5:{t_title} {t_artist}".strip()
        song = Song(
            {
                'title': title_str,
                'webpage_url': track_item.get('url') or query,
                'url': search_q,
                'duration': track_item.get('duration', 0) or 0,
                'thumbnail': track_item.get('thumbnail') or playlist_thumb,
                'uploader': t_artist or platform_name,
            },
            interaction.user,
        )
        state.queue.append(song)
        added_count += 1

    type_label = "播放清單" if ext_res.get('type') == 'playlist' else "專輯"
    embed = discord.Embed(
        title=f"🎶 【{selected_bot.node_name}】成功匯入 {platform_name} {type_label}！",
        description=f"**[{playlist_title}]({query})**",
        color=embed_color,
    )
    if playlist_thumb:
        embed.set_thumbnail(url=playlist_thumb)
    embed.add_field(name="🔢 成功加入", value=f"`{added_count}` 首歌曲", inline=True)
    embed.add_field(name="📍 當前隊列長度", value=f"`{len(state.queue)}` 首", inline=True)
    embed.add_field(name="🤖 服務節點", value=f"`{selected_bot.node_name}`", inline=True)
    embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
    await interaction.followup.send(embed=embed)

    if not is_vc_active(vc):
        await play_next(selected_bot, bot_guild)


async def _search_first_playable_entry(search_q: str) -> Optional[Dict[str, Any]]:
    try:
        data = await run_media(
            lambda q=search_q: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(q, download=False)
        )
        if data and 'entries' in data and data['entries']:
            for entry in data['entries']:
                if entry and isinstance(entry, dict) and (entry.get('url') or entry.get('formats')):
                    return entry
        elif data and isinstance(data, dict) and (data.get('url') or data.get('formats')):
            return data
    except Exception:
        return None
    return None


async def _resolve_external_service(
    query: str, is_spotify: bool, is_netease: bool
) -> Tuple[Optional[Dict[str, Any]], str]:
    if is_spotify:
        ext_res = await resolve_spotify(query)
        if ext_res:
            ext_res.setdefault('platform', 'spotify')
            ext_res.setdefault('platform_name', 'Spotify')
            ext_res.setdefault('color', 0x1DB954)
        return ext_res, "Spotify"
    if is_netease:
        return await resolve_netease(query), "網易雲音樂"
    return await resolve_apple_music(query), "Apple Music"


async def _handle_external_platform_play(
    interaction: discord.Interaction,
    selected_bot: MusicNodeBot,
    bot_guild: discord.Guild,
    vc: Any,
    state: GuildMusicState,
    voice_channel: Any,
    query: str,
    already_in_room: bool,
    is_spotify: bool,
    is_netease: bool,
) -> None:
    ext_res, platform_label = await _resolve_external_service(query, is_spotify, is_netease)
    if not ext_res or not ext_res.get('tracks'):
        await interaction.followup.send(
            f"❌ 無法解析該 {platform_label} 連結，請確認該歌曲或播放清單為公開狀態！"
        )
        return

    if ext_res.get('type') in ('playlist', 'album') or len(ext_res.get('tracks', [])) > 1:
        await _import_external_playlist(interaction, selected_bot, bot_guild, vc, state, query, ext_res)
        return

    valid_tracks = [tr for tr in ext_res.get('tracks', []) if tr and isinstance(tr, dict)]
    if not valid_tracks:
        await interaction.followup.send(f"❌ 無法解析該 {platform_label} 連結中的歌曲資訊！")
        return

    track_item = valid_tracks[0]
    platform_name = str(ext_res.get('platform_name', '音樂平台'))
    t_title = str(track_item.get('title') or '未知歌曲')
    t_artist = str(track_item.get('artist') or '')
    title_str = f"{t_title} - {t_artist}" if t_artist else t_title

    yt_data = await _search_first_playable_entry(f"ytsearch5:{t_title} {t_artist}".strip()) or (
        await _search_first_playable_entry(f"scsearch3:{t_title} {t_artist}".strip())
    )
    if not yt_data:
        await interaction.followup.send(f"❌ 找不到該 {platform_name} 歌曲 **{title_str}** 的音源！")
        return

    yt_data['title'] = title_str
    yt_data['webpage_url'] = track_item.get('url') or query
    if track_item.get('thumbnail'):
        yt_data['thumbnail'] = track_item['thumbnail']
    if track_item.get('duration'):
        yt_data['duration'] = track_item['duration']
    yt_data['uploader'] = t_artist or platform_name

    song = Song(yt_data, interaction.user)
    await _enqueue_or_start_single_song(
        interaction, selected_bot, bot_guild, vc, state, voice_channel, song, already_in_room
    )


def _resolve_playlist_entry_url(entry: Dict[str, Any]) -> str:
    song_url = entry.get('webpage_url')
    if song_url:
        return str(song_url)
    vid = entry.get('id')
    vid_str = str(vid) if vid is not None else ''
    if vid_str and re.match(r'^[a-zA-Z0-9_-]{11}$', vid_str):
        return f"https://www.youtube.com/watch?v={vid_str}"
    raw_url = entry.get('url', '') or ''
    raw_str = str(raw_url) if raw_url is not None else ''
    if raw_str.startswith('http'):
        return raw_str
    if raw_str and re.match(r'^[a-zA-Z0-9_-]{11}$', raw_str):
        return f"https://www.youtube.com/watch?v={raw_str}"
    return ''


async def _handle_youtube_playlist_play(
    interaction: discord.Interaction,
    selected_bot: MusicNodeBot,
    bot_guild: discord.Guild,
    vc: Any,
    state: GuildMusicState,
    query: str,
) -> None:
    try:
        data = await run_media(
            lambda q=query: get_thread_ytdl(extract_flat=True, noplaylist=False).extract_info(q, download=False)
        )
    except Exception as exc:
        await interaction.followup.send(f"❌ 讀取播放清單失敗: `{exc}`")
        return

    if not data or not isinstance(data, dict):
        await interaction.followup.send("❌ 讀取播放清單失敗，查無內容！")
        return

    entries = data.get('entries', [])
    valid = [
        item
        for item in entries
        if item and isinstance(item, dict) and (item.get('title') or item.get('url'))
    ]
    if not valid:
        await interaction.followup.send("❌ 該播放清單中未找到任何有效歌曲！")
        return

    added_count = 0
    playlist_title = str(data.get('title') or '播放清單')
    for entry in valid[: min(200, state.queue_space())]:
        title = str(entry.get('title') or '未知歌曲')
        if title in ('[Deleted video]', '[Private video]'):
            continue
        song_url = _resolve_playlist_entry_url(entry)
        song = Song(
            {
                'id': entry.get('id'),
                'title': title,
                'webpage_url': song_url,
                'url': song_url,
                'duration': entry.get('duration', 0) or 0,
                'thumbnail': entry.get('thumbnail', '') or '',
            },
            interaction.user,
        )
        state.queue.append(song)
        added_count += 1

    embed = discord.Embed(
        title=f"📑 【{selected_bot.node_name}】成功匯入播放清單！",
        description=f"**[{playlist_title}]({query})**",
        color=0x2ECC71,
    )
    embed.add_field(name="🔢 成功加入", value=f"`{added_count}` 首歌曲", inline=True)
    embed.add_field(name="📍 當前隊列長度", value=f"`{len(state.queue)}` 首", inline=True)
    embed.add_field(name="🤖 服務節點", value=f"`{selected_bot.node_name}`", inline=True)
    embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
    await interaction.followup.send(embed=embed)

    if not is_vc_active(vc):
        await play_next(selected_bot, bot_guild)


async def _extract_single_track_info(
    interaction: discord.Interaction,
    query: str,
) -> Optional[Dict[str, Any]]:
    data: Any = None
    try:
        data = await run_media(
            lambda q=query: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(q, download=False)
        )
    except Exception as first_err:
        try:
            sc_q = f"scsearch:{query}"
            data = await run_media(
                lambda q=sc_q: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(q, download=False)
            )
        except Exception:
            await interaction.followup.send(f"❌ 找不到該歌曲或解析失敗: `{first_err}`")
            return None

    if not data:
        try:
            sc_q = f"scsearch:{query}"
            data = await run_media(
                lambda q=sc_q: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(q, download=False)
            )
        except Exception:
            logger.debug("Ignored exception in slash_play fallback search", exc_info=True)

    if not data or not isinstance(data, dict):
        await interaction.followup.send("❌ 找不到該歌曲或解析結果為空，請更換關鍵字！")
        return None

    if 'entries' in data and data.get('entries') is not None:
        valid_entries = [item for item in data['entries'] if item and isinstance(item, dict)]
        if not valid_entries:
            await interaction.followup.send("❌ 搜尋結果為空，請更換關鍵字！")
            return None
        data = valid_entries[0]

    if not data or not isinstance(data, dict):
        await interaction.followup.send("❌ 找不到有效歌曲資訊！")
        return None

    return data


async def _handle_play(
    interaction: discord.Interaction,
    query: str,
    bot_id: Optional[int] = None,
) -> None:
    await interaction.response.defer()

    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.followup.send(t('must_join_voice', interaction))
        return

    resolved = await _resolve_bot_and_channel_for_play(interaction, voice_channel, bot_id)
    if not resolved:
        return
    selected_bot, bot_guild, bot_voice_channel, state, already_in_room = resolved

    vc, connected_ok = await _connect_or_move_bot_vc(
        interaction, selected_bot, bot_guild, bot_voice_channel, voice_channel, state, already_in_room
    )
    if not connected_ok:
        return

    if await _try_lavalink_direct_play(
        interaction, selected_bot, bot_guild, vc, state, voice_channel, query, already_in_room
    ):
        return

    is_spotify = bool(SPOTIFY_URL_REGEX.search(query))
    is_netease = bool(
        NETEASE_URL_REGEX.search(query)
        or "163cn.tv" in query
        or "163.lu" in query
        or "music.163.com" in query
    )
    is_apple = bool(
        APPLE_MUSIC_URL_REGEX.search(query) or "apple.co" in query or "music.apple.com" in query
    )

    if is_spotify or is_netease or is_apple:
        await _handle_external_platform_play(
            interaction,
            selected_bot,
            bot_guild,
            vc,
            state,
            voice_channel,
            query,
            already_in_room,
            is_spotify,
            is_netease,
        )
        return

    if ("playlist" in query.lower()) or ("list=" in query.lower()):
        await _handle_youtube_playlist_play(interaction, selected_bot, bot_guild, vc, state, query)
        return

    track_data = await _extract_single_track_info(interaction, query)
    if not track_data:
        return

    song = Song(track_data, interaction.user)
    await _enqueue_or_start_single_song(
        interaction, selected_bot, bot_guild, vc, state, voice_channel, song, already_in_room
    )


async def _fetch_google_yt_suggestions(cur_stripped: str) -> List[str]:
    cached = get_cached_autocomplete(cur_stripped)
    if cached is not None:
        return cached

    raw_suggestions: List[str] = []
    try:
        session = await get_http_session()
        q_enc = urllib.parse.quote(cur_stripped)
        api_url = f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={q_enc}"
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=1.0)) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                    for s in data[1][:7]:
                        if s and isinstance(s, str) and s not in raw_suggestions:
                            raw_suggestions.append(s)
                    set_cached_autocomplete(cur_stripped, raw_suggestions)
    except Exception:
        logger.debug("Ignored exception in _fetch_google_yt_suggestions", exc_info=True)
    return raw_suggestions


def _append_history_choices(
    state: Optional[GuildMusicState],
    cur_stripped: str,
    choices: List[app_commands.Choice[str]],
) -> None:
    if not (state and state.history):
        return
    seen = set()
    existing_values = {c.value for c in choices}
    for song in state.history:
        if song.title and song.title not in seen:
            seen.add(song.title)
            if (not cur_stripped or cur_stripped.lower() in song.title.lower()) and (
                song.title[:100] not in existing_values
            ):
                choices.append(app_commands.Choice(name=f"🕒 {song.title[:95]}", value=song.title[:100]))
                existing_values.add(song.title[:100])
                if len(choices) >= 10:
                    break


async def _compute_autocomplete_choices(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    state = NODE_BOTS[0].get_state(interaction.guild_id) if (NODE_BOTS and interaction.guild_id) else None
    choices: List[app_commands.Choice[str]] = []
    cur_stripped = current.strip()

    if cur_stripped:
        suggestions = await _fetch_google_yt_suggestions(cur_stripped)
        choices.extend(app_commands.Choice(name=f"🔍 {s[:95]}", value=s[:100]) for s in suggestions)

    _append_history_choices(state, cur_stripped, choices)

    if not cur_stripped and not choices:
        default_suggestions = [
            "周杰倫 經典熱門歌曲",
            "Lofi Hip Hop chill beats",
            "Trending Pop Music 2026",
            "華語流行音樂排行榜",
            "Billboard Hot 100",
        ]
        choices.extend(app_commands.Choice(name=f"🔥 {s}", value=s) for s in default_suggestions)

    if cur_stripped and not choices:
        choices.append(app_commands.Choice(name=f"🔍 {cur_stripped[:95]}", value=cur_stripped[:100]))

    return choices[:10]


async def _handle_search(interaction: discord.Interaction, keyword: str) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    await interaction.response.defer()
    selected_bot, is_new_join = await find_best_bot_for_channel(
        interaction.guild_id, voice_channel, interaction.guild
    )
    if not selected_bot:
        await interaction.followup.send(t('no_bot_available', interaction))
        return

    bot_guild = get_bot_guild(selected_bot, interaction.guild_id, interaction.guild) or interaction.guild
    vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(
        getattr(selected_bot, 'voice_clients', []), guild__id=interaction.guild_id
    )

    if is_new_join or not vc or not is_vc_connected(vc):
        try:
            vc = await safe_voice_connect(selected_bot, voice_channel, timeout=20.0)
        except Exception as e:
            await interaction.followup.send(
                t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e))
            )
            return

    query = f"ytsearch5:{keyword}"
    try:
        data = await run_media(
            lambda q=query: get_thread_ytdl(extract_flat=True, noplaylist=True).extract_info(q, download=False)
        )
    except Exception as e:
        await interaction.followup.send(f"❌ 搜尋失敗: `{e}`")
        return

    raw_entries = data.get('entries', []) if (data and isinstance(data, dict)) else []
    entries = [item for item in raw_entries if item and isinstance(item, dict) and item.get('title')][:5]
    if not entries:
        await interaction.followup.send(f"❌ 找不到與 `{keyword}` 相關的搜尋結果！")
        return

    embed = discord.Embed(
        title=f"🔍 【{selected_bot.node_name}】搜尋結果 — 請選擇歌曲",
        description=f"關鍵字：`{keyword}`\n請於下方選單點選您欲點播的歌曲：",
        color=0x5865F2,
    )
    for i, item in enumerate(entries):
        dur = format_duration(item.get('duration', 0))
        embed.add_field(
            name=f"{i + 1}. {str(item.get('title', '未知'))[:60]}",
            value=f"⏱️ 時長: `{dur}` | 👤 上傳者: `{str(item.get('uploader', '未知'))[:30]}`",
            inline=False,
        )

    view = SearchSelectView(selected_bot, bot_guild, interaction.user, entries)
    msg = await interaction.followup.send(embed=embed, view=view)
    view.message = msg


async def _handle_skip(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    if not has_dj_permission(interaction, state):
        await interaction.response.send_message(
            f"❌ 該指令目前僅限擁有 DJ 身分組 <@&{state.dj_role_id}> 或管理員使用！", ephemeral=True
        )
        return

    bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
    vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(
        getattr(b, 'voice_clients', []), guild__id=interaction.guild_id
    )
    if vc and is_vc_active(vc):
        state.skip_requested = True
        if state.loop_mode == 'single':
            state.loop_mode = 'off'
        await safe_stop(vc)
        await interaction.response.send_message(t('skipped', interaction, bot_name=b.node_name))
    else:
        await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)


async def _handle_force_stop_all(interaction: discord.Interaction, guild_id: Optional[int]) -> None:
    perms = getattr(interaction.user, 'guild_permissions', None)
    if not (perms and getattr(perms, 'administrator', False)):
        await interaction.response.send_message("❌ 強制全體退出指令僅限伺服器管理員使用！", ephemeral=True)
        return
    stopped_names: List[str] = []
    for node_bot in NODE_BOTS:
        bg = get_bot_guild(node_bot, guild_id, interaction.guild)
        if bg:
            b_vc = bg.voice_client or discord.utils.get(
                getattr(node_bot, 'voice_clients', []), guild__id=guild_id
            )
            if b_vc and is_vc_connected(b_vc):
                b_state = node_bot.get_state(guild_id)
                b_state.skip_requested = True
                b_state.cleanup()
                await safe_stop(b_vc)
                await safe_disconnect(b_vc)
                stopped_names.append(node_bot.node_name)
    if stopped_names:
        await interaction.response.send_message(
            t('force_stopped_all', interaction, names=', '.join(stopped_names))
        )
    else:
        await interaction.response.send_message(t('no_bots_connected', interaction), ephemeral=True)


async def _handle_stop(interaction: discord.Interaction, force_all: bool = False) -> None:
    guild_id = interaction.guild_id
    if force_all:
        await _handle_force_stop_all(interaction, guild_id)
        return

    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    if not has_dj_permission(interaction, state):
        await interaction.response.send_message(
            f"❌ 該指令目前僅限擁有 DJ 身分組 <@&{state.dj_role_id}> 或管理員使用！", ephemeral=True
        )
        return

    state.skip_requested = True
    state.cleanup()
    bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
    vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(
        getattr(b, 'voice_clients', []), guild__id=interaction.guild_id
    )
    if vc:
        await safe_stop(vc)
        await safe_disconnect(vc)
    await interaction.response.send_message(t('stopped_and_left', interaction, bot_name=b.node_name))


async def _handle_nowplaying(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    if not state.current:
        await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
        return

    embed = build_nowplaying_embed(b, state.current, state, lang=resolve_locale(interaction))
    if state.current_view:
        try:
            state.current_view.stop()
        except Exception:
            logger.debug("Ignored exception in slash_nowplaying", exc_info=True)
    old_msg = state.nowplaying_message
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            logger.debug("Ignored exception in slash_nowplaying", exc_info=True)
    view = MusicControllerView(b, state)
    state.current_view = view
    await interaction.response.send_message(embed=embed, view=view)
    try:
        state.nowplaying_message = await interaction.original_response()
    except Exception:
        logger.debug("Ignored exception in slash_nowplaying", exc_info=True)


async def _handle_queue(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    lang = resolve_locale(interaction)
    embed = build_queue_embed(b, state, lang=lang, page=1)
    view = QueuePaginationView(b, state, lang=lang)
    await interaction.response.send_message(embed=embed, view=view)
    try:
        view.message = await interaction.original_response()
    except Exception:
        logger.debug("Failed to get original_response for QueuePaginationView", exc_info=True)


async def _handle_move(interaction: discord.Interaction, from_pos: int, to_pos: int) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    if not has_dj_permission(interaction, state):
        await interaction.response.send_message(
            t('dj_permission_denied', interaction, role_id=state.dj_role_id), ephemeral=True
        )
        return

    q_len = len(state.queue)
    if q_len == 0:
        await interaction.response.send_message(t('queue_empty', interaction), ephemeral=True)
        return

    if from_pos < 1 or from_pos > q_len or to_pos < 1 or to_pos > q_len or from_pos == to_pos:
        await interaction.response.send_message(
            t('move_invalid_range', interaction, max_idx=q_len), ephemeral=True
        )
        return

    queue_list = list(state.queue)
    target_song = queue_list.pop(from_pos - 1)
    queue_list.insert(to_pos - 1, target_song)
    state.queue = deque(queue_list)
    state.last_active = time.time()

    embed = discord.Embed(
        title="🔀 隊列順序調整成功！",
        description=t('move_success', interaction, src=from_pos, dst=to_pos)
        + f"\n**[{target_song.title}]({target_song.url})**",
        color=0x3498DB,
    )
    await interaction.response.send_message(embed=embed)


async def _ensure_vc_connected(
    selected_bot: Any,
    bot_guild: Optional[discord.Guild],
    guild_id: Optional[int],
    voice_channel: Any,
    is_new_join: bool,
    timeout: float,
) -> Any:
    vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(
        getattr(selected_bot, 'voice_clients', []), guild__id=guild_id
    )
    if is_new_join or not vc or not is_vc_connected(vc):
        vc = await safe_voice_connect(selected_bot, voice_channel, timeout=timeout)
    return vc


async def _handle_replay(interaction: discord.Interaction, position: int = 1) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    selected_bot, is_new_join = await find_best_bot_for_channel(
        interaction.guild_id, voice_channel, interaction.guild
    )
    if not selected_bot:
        await interaction.response.send_message(t('no_bot_available', interaction), ephemeral=True)
        return

    state = selected_bot.get_state(interaction.guild_id)
    if not state.history:
        await interaction.response.send_message(t('history_empty', interaction), ephemeral=True)
        return
    if position < 1 or position > len(state.history):
        await interaction.response.send_message(t('replay_invalid_index', interaction), ephemeral=True)
        return

    bot_guild = get_bot_guild(selected_bot, interaction.guild_id, interaction.guild) or interaction.guild
    try:
        vc = await _ensure_vc_connected(
            selected_bot, bot_guild, interaction.guild_id, voice_channel, is_new_join, 20.0
        )
    except Exception as e:
        await interaction.response.send_message(
            t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e)),
            ephemeral=True,
        )
        return

    if state.queue_space() <= 0:
        await interaction.response.send_message(
            t('queue_full', interaction, max_size=MAX_QUEUE_SIZE), ephemeral=True
        )
        return

    history_song = state.history[position - 1]
    new_song = Song(
        history_song.data, requester=interaction.user, wavelink_track=history_song.wavelink_track
    )
    new_song.stream_url = history_song.stream_url
    new_song.duration = history_song.duration
    new_song.thumbnail = history_song.thumbnail

    if interaction.channel and hasattr(interaction.channel, 'send'):
        state.text_channel = interaction.channel
    state.queue.append(new_song)

    embed = discord.Embed(
        title=f"🔄 【{selected_bot.node_name}】重新加入播放隊列！",
        description=f"**[{new_song.title}]({new_song.url})**",
        color=0x2ECC71,
    )
    embed.add_field(name="⏱️ 時長", value=f"`{format_duration(new_song.duration)}`", inline=True)
    embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
    embed.add_field(name="📍 隊列位置", value=f"`第 {len(state.queue)} 位`", inline=True)
    if new_song.thumbnail:
        embed.set_thumbnail(url=new_song.thumbnail)
    await interaction.response.send_message(embed=embed)

    if not is_vc_active(vc):
        await play_next(selected_bot, bot_guild)


async def _fav_list(interaction: discord.Interaction, uid: int) -> None:
    favs = get_user_favorites(uid)
    if not favs:
        await interaction.response.send_message(
            "⭐ 您的個人收藏清單目前是空的！\n播放音樂時可點擊控制面板上的 `❤️ 收藏` 按鈕或使用 `/favorite 動作: 收藏當前播放歌曲` 將喜愛歌曲加入！",
            ephemeral=True,
        )
        return
    embed = discord.Embed(
        title=f"❤️ {interaction.user.display_name} 的個人最愛收藏歌單 (共 {len(favs)} 首)",
        color=0xFF6B81,
    )
    lines = []
    for idx, item in enumerate(favs[:25], start=1):
        dur_str = format_duration(item.get("duration", 0))
        t_str = str(item.get("title", "未知歌曲"))[:45]
        u_str = str(item.get("url", ""))
        if u_str and u_str.startswith("http"):
            lines.append(f"`{idx:02d}.` **[{t_str}]({u_str})** `[{dur_str}]`")
        else:
            lines.append(f"`{idx:02d}.` **{t_str}** `[{dur_str}]`")
    if len(favs) > 25:
        lines.append(f"\n*...以及其他 **{len(favs) - 25}** 首收藏歌曲*")
    embed.description = "\n".join(lines)
    embed.set_footer(text="💡 使用 /favorite 動作: 一鍵播放全部收藏 即可整批加入隊列播放！")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def _fav_add(interaction: discord.Interaction, uid: int) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return
    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return
    state = b.get_state(interaction.guild_id)
    if not state.current:
        await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
        return
    cur = state.current
    song_item = {
        "title": cur.title,
        "url": cur.url or f"ytsearch5:{cur.title}",
        "duration": cur.duration or 0,
        "thumbnail": cur.thumbnail or "",
        "uploader": cur.uploader or "",
    }
    added, total_count = add_user_favorite(uid, song_item)
    if added:
        await interaction.response.send_message(
            f"❤️ 已成功將 **{cur.title}** 加入您的個人最愛收藏！（目前共 `{total_count}` 首）",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"💡 **{cur.title}** 已經在您的個人最愛收藏中囉！（共 `{total_count}` 首）",
            ephemeral=True,
        )


async def _fav_remove(interaction: discord.Interaction, uid: int, index: Optional[int]) -> None:
    favs = get_user_favorites(uid)
    if not favs:
        await interaction.response.send_message("❌ 您的個人收藏清單目前為空！", ephemeral=True)
        return
    if not index or index < 1 or index > len(favs):
        await interaction.response.send_message(
            f"❌ 請輸入有效的收藏歌曲編號 (`1 ~ {len(favs)}`)！", ephemeral=True
        )
        return
    removed = remove_user_favorite(uid, index)
    if removed:
        await interaction.response.send_message(
            f"🗑️ 已從個人收藏中移除第 `{index}` 首歌曲：**{removed.get('title', '未知')}**！",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message("❌ 移除失敗，請確認編號是否正確！", ephemeral=True)


async def _fav_clear(interaction: discord.Interaction, uid: int) -> None:
    count = clear_user_favorites(uid)
    if count == 0:
        await interaction.response.send_message("❌ 您的個人收藏清單已經是空的！", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"🧹 已成功清空您的個人最愛收藏（共清除 `{count}` 首歌曲）！", ephemeral=True
        )


def _fav_items_to_songs(favs: List[Dict[str, Any]], limit: int, requester: Any) -> List[Song]:
    songs: List[Song] = []
    for item in favs[:limit]:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        songs.append(
            Song(
                {
                    "title": item.get("title"),
                    "webpage_url": item.get("url"),
                    "url": item.get("url") or f"ytsearch5:{item.get('title')}",
                    "duration": item.get("duration", 0),
                    "thumbnail": item.get("thumbnail", ""),
                    "uploader": item.get("uploader", "個人收藏"),
                },
                requester,
            )
        )
    return songs


async def _fav_play(interaction: discord.Interaction, uid: int) -> None:
    favs = get_user_favorites(uid)
    if not favs:
        await interaction.response.send_message(
            "⭐ 您的個人收藏清單目前是空的！先按控制面板上的 `❤️ 收藏` 將喜歡的歌曲存起來吧！",
            ephemeral=True,
        )
        return
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return
    await interaction.response.defer()
    selected_bot, is_new_join = await find_best_bot_for_channel(
        interaction.guild_id, voice_channel, interaction.guild
    )
    if not selected_bot:
        await interaction.followup.send(t('no_bot_available', interaction))
        return
    bot_guild = get_bot_guild(selected_bot, interaction.guild_id, interaction.guild) or interaction.guild
    try:
        vc = await _ensure_vc_connected(
            selected_bot, bot_guild, interaction.guild_id, voice_channel, is_new_join, 25.0
        )
    except Exception as e:
        await interaction.followup.send(
            t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e))
        )
        return
    state = selected_bot.get_state(interaction.guild_id)
    if state.queue_space() <= 0:
        await interaction.followup.send(t('queue_full', interaction, max_size=MAX_QUEUE_SIZE))
        return
    if interaction.channel and hasattr(interaction.channel, 'send'):
        state.text_channel = interaction.channel
    new_songs = _fav_items_to_songs(favs, state.queue_space(), interaction.user)
    state.queue.extend(new_songs)
    embed = discord.Embed(
        title=f"❤️ 【{selected_bot.node_name}】已匯入個人最愛收藏歌單！",
        description=f"已將 **{interaction.user.display_name}** 的 **`{len(new_songs)}`** 首收藏曲目排入待播清單！",
        color=0xFF6B81,
    )
    embed.add_field(name="📍 當前隊列總數", value=f"`{len(state.queue)}` 首", inline=True)
    embed.add_field(name="🤖 服務節點", value=f"`{selected_bot.node_name}`", inline=True)
    embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
    await interaction.followup.send(embed=embed)
    if not is_vc_active(vc):
        await play_next(selected_bot, bot_guild)


async def _handle_favorite(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    index: Optional[int] = None,
) -> None:
    act = action.value
    uid = interaction.user.id
    if act == "list":
        await _fav_list(interaction, uid)
    elif act == "add":
        await _fav_add(interaction, uid)
    elif act == "remove":
        await _fav_remove(interaction, uid, index)
    elif act == "clear":
        await _fav_clear(interaction, uid)
    elif act == "play":
        await _fav_play(interaction, uid)


def _register_queue_and_fav_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="nowplaying", description="查看你所在語音房當前歌曲詳情與控制器")
    async def slash_nowplaying(interaction: discord.Interaction) -> None:
        await _handle_nowplaying(interaction)

    @tree.command(name="queue", description="查看你所在語音房的待播歌單 (支援互動翻頁)")
    async def slash_queue(interaction: discord.Interaction) -> None:
        await _handle_queue(interaction)

    @tree.command(name="move", description="調整待播清單中指定歌曲的播放順序 (Move song position in queue)")
    @app_commands.describe(from_pos="要移動的歌曲在待播清單中的序號", to_pos="要移至的新序號位置")
    @app_commands.rename(from_pos="來源序號", to_pos="目標序號")
    async def slash_move(interaction: discord.Interaction, from_pos: int, to_pos: int) -> None:
        await _handle_move(interaction, from_pos, to_pos)

    @tree.command(name="replay", description="重播歷史紀錄中的歌曲 (Replay a song from history)")
    @app_commands.describe(position="歷史紀錄中的歌曲序號 (預設為 1，即上一首)")
    @app_commands.rename(position="序號")
    async def slash_replay(interaction: discord.Interaction, position: int = 1) -> None:
        await _handle_replay(interaction, position)

    @tree.command(name="favorite", description="管理與一鍵播放您的個人最愛收藏歌單 (Personal favorites playlist)")
    @app_commands.describe(
        action="選擇要執行的操作 (播放全部 / 查看清單 / 收藏當前歌曲 / 移除指定序號 / 清空全部)",
        index="移除收藏時的歌曲編號 (僅在選擇「移除指定歌曲」時需要填寫)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="▶️ 一鍵播放全部收藏 (Play All Favorites)", value="play"),
        app_commands.Choice(name="📜 查看個人收藏清單 (View List)", value="list"),
        app_commands.Choice(name="❤️ 收藏當前播放歌曲 (Add Current Song)", value="add"),
        app_commands.Choice(name="🗑️ 移除指定編號歌曲 (Remove by Index)", value="remove"),
        app_commands.Choice(name="🧹 清空個人收藏清單 (Clear All)", value="clear"),
    ])
    @app_commands.rename(action="動作", index="編號")
    async def slash_favorite(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        index: Optional[int] = None,
    ) -> None:
        await _handle_favorite(interaction, action, index)


def register_music_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="play", description="智能點播歌曲（自動分流派發至空閒的機器人進房播放）")
    @app_commands.describe(
        query="歌曲名稱或影片/歌單網址",
        bot_id="可選：指定派遣 1 ~ 5 號機器人 (留空則系統自動智能派工)",
    )
    @app_commands.rename(query="查詢", bot_id="指定機器人")
    @app_commands.checks.cooldown(1, COMMAND_COOLDOWN, key=lambda i: (i.guild_id, i.user.id))
    async def slash_play(
        interaction: discord.Interaction, query: str, bot_id: Optional[int] = None
    ) -> None:
        await _handle_play(interaction, query, bot_id)

    @slash_play.autocomplete("query")
    async def slash_play_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await _compute_autocomplete_choices(interaction, current)

    @tree.command(name="search", description="搜尋歌曲並從前 5 個結果中挑選播放 (Search & pick from top 5)")
    @app_commands.describe(keyword="要搜尋的歌曲名稱或歌手")
    @app_commands.rename(keyword="關鍵字")
    @app_commands.checks.cooldown(1, COMMAND_COOLDOWN, key=lambda i: (i.guild_id, i.user.id))
    async def slash_search(interaction: discord.Interaction, keyword: str) -> None:
        await _handle_search(interaction, keyword)

    @slash_search.autocomplete("keyword")
    async def slash_search_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await _compute_autocomplete_choices(interaction, current)

    @tree.command(name="skip", description="跳過你當前語音房的機器人歌曲")
    async def slash_skip(interaction: discord.Interaction) -> None:
        await _handle_skip(interaction)

    @tree.command(name="stop", description="停止音樂機器人並讓其離開語音房")
    @app_commands.describe(force_all="是否強制讓本伺服器所有音樂機器人全數停止並退出語音房？")
    @app_commands.rename(force_all="全部退出")
    async def slash_stop(interaction: discord.Interaction, force_all: bool = False) -> None:
        await _handle_stop(interaction, force_all)

    _register_queue_and_fav_commands(tree)

