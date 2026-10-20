import asyncio
import re
import time
from collections import deque
from typing import Optional, List, Dict, Tuple
import discord
from discord import app_commands
import urllib.parse
from config import (
    MEDIA_EXECUTOR,
    SPOTIFY_URL_REGEX,
    NETEASE_URL_REGEX,
    APPLE_MUSIC_URL_REGEX,
    HAS_WAVELINK,
    wavelink,
    get_thread_ytdl,
    get_http_session
)
from i18n import t, resolve_locale
from core.models import (
    Song,
    is_vc_connected,
    is_vc_active,
    has_dj_permission,
    safe_voice_connect
)
from core.node import NODE_BOTS, MusicNodeBot
from core.dispatcher import (
    get_bot_guild,
    find_best_bot_for_channel,
    get_bot_in_user_room,
    get_user_voice_channel
)
from core.player import (
    play_next,
    safe_stop,
    safe_disconnect
)
from platforms.spotify import resolve_spotify
from platforms.netease import resolve_netease
from platforms.apple_music import resolve_apple_music
from ui.embeds import (
    build_nowplaying_embed,
    build_queue_embed,
    build_song_queued_embed,
    format_duration
)
from ui.views import (
    MusicControllerView,
    SearchSelectView
)

_AUTOCOMPLETE_CACHE: Dict[str, Tuple[List[str], float]] = {}

def register_music_commands(tree: app_commands.CommandTree):
    @tree.command(name="play", description="智能點播歌曲（自動分流派發至空閒的機器人進房播放）")
    @app_commands.describe(
        查詢="歌曲名稱或影片/歌單網址",
        指定機器人="可選：指定派遣 1 ~ 5 號機器人 (留空則系統自動智能派工)"
    )
    async def slash_play(interaction: discord.Interaction, 查詢: str, 指定機器人: Optional[int] = None):
        await interaction.response.defer()

        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.followup.send(t('must_join_voice', interaction))
            return

        guild_id = interaction.guild_id

        selected_bot: Optional[MusicNodeBot] = None
        already_in_room = False

        if 指定機器人 is not None:
            for b in NODE_BOTS:
                if b.node_id == 指定機器人:
                    selected_bot = b
                    break
            if not selected_bot:
                await interaction.followup.send(f"❌ 找不到 【音樂 {指定機器人} 號】，目前上線的機器人數量為 {len(NODE_BOTS)} 隻！")
                return
        else:
            selected_bot, already_in_room = await find_best_bot_for_channel(guild_id, voice_channel, interaction.guild)

        if not selected_bot:
            guild_bots = []
            for b in NODE_BOTS:
                bg = get_bot_guild(b, guild_id, interaction.guild)
                if bg:
                    guild_bots.append((b, bg))

            total_active = len(guild_bots)

            if total_active <= 1:
                cur_b, cur_bg = guild_bots[0] if guild_bots else (NODE_BOTS[0], interaction.guild)
                cur_name = cur_b.node_name
                cur_vc = cur_bg.voice_client if cur_bg else None
                cur_room_name = f"`#{cur_vc.channel.name}`" if (cur_vc and hasattr(cur_vc, 'channel') and cur_vc.channel) else "其他房間"

                msg = t('only_one_bot_busy', interaction, bot_name=cur_name, room_name=cur_room_name)
                await interaction.followup.send(msg)
            else:
                await interaction.followup.send(t('no_bot_available', interaction))
            return

        bot_guild = get_bot_guild(selected_bot, guild_id, interaction.guild) or interaction.guild
        if not bot_guild:
            await interaction.followup.send(t('bot_not_in_guild', interaction, bot_name=selected_bot.node_name))
            return

        bot_voice_channel = bot_guild.get_channel(voice_channel.id) or voice_channel
        state = selected_bot.get_state(guild_id)
        state.text_channel = interaction.channel

        try:
            max_bitrate = bot_voice_channel.guild.bitrate_limit
            if bot_voice_channel.bitrate < max_bitrate:
                await bot_voice_channel.edit(bitrate=max_bitrate)
        except Exception:
            pass

        vc = bot_guild.voice_client
        if not vc:
            for v in getattr(selected_bot, 'voice_clients', []):
                if v.guild and v.guild.id == guild_id:
                    vc = v
                    break
        is_wavelink_player = HAS_WAVELINK and hasattr(vc, 'filters')
        should_be_wavelink = selected_bot.lavalink_connected and HAS_WAVELINK

        if vc and is_vc_connected(vc) and (is_wavelink_player != should_be_wavelink):
            await safe_disconnect(vc)
            vc = None

        if not vc or not is_vc_connected(vc):
            try:
                if should_be_wavelink:
                    try:
                        vc = await safe_voice_connect(selected_bot, bot_voice_channel, cls=wavelink.Player)
                    except Exception as wl_conn_err:
                        print(f"[{selected_bot.node_name}] Lavalink Player 連線異常 ({wl_conn_err})，使用原生語音連線...")
                        vc = await safe_voice_connect(selected_bot, bot_voice_channel, timeout=30.0)
                else:
                    vc = await safe_voice_connect(selected_bot, bot_voice_channel, timeout=30.0)
            except Exception as e:
                await interaction.followup.send(t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e)))
                return
        elif hasattr(vc, 'channel') and vc.channel and vc.channel.id != voice_channel.id:
            if not already_in_room:
                state.loop_mode = 'off'
                state.current = None
                state.queue.clear()
                state.is_playing = False
                await safe_stop(vc)
            await vc.move_to(bot_voice_channel)

        is_custom_music_service = bool(
            SPOTIFY_URL_REGEX.search(查詢) or
            NETEASE_URL_REGEX.search(查詢) or
            APPLE_MUSIC_URL_REGEX.search(查詢) or
            ("163cn.tv" in 查詢 or "163.lu" in 查詢 or "apple.co" in 查詢) or
            ("playlist" in 查詢.lower() or "list=" in 查詢.lower())
        )
        if selected_bot.lavalink_connected and HAS_WAVELINK and not is_custom_music_service:
            try:
                tracks: wavelink.Search = await wavelink.Playable.search(查詢)
                if tracks:
                    wl_track = tracks[0]
                    song = Song({
                        'title': wl_track.title,
                        'url': wl_track.uri or 查詢,
                        'duration': int(wl_track.length // 1000) if hasattr(wl_track, 'length') else 0,
                        'thumbnail': wl_track.artwork or '',
                        'uploader': wl_track.author or '未知'
                    }, interaction.user, wavelink_track=wl_track)

                    if is_vc_active(vc):
                        state.queue.append(song)
                        embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
                        await interaction.followup.send(embed=embed)
                    else:
                        state.queue.append(song)
                        if not already_in_room:
                            await interaction.followup.send(t('song_dispatched', interaction, title=song.title, bot_name=selected_bot.node_name, channel=voice_channel.mention))
                        else:
                            await interaction.followup.send(t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name))
                        await play_next(selected_bot, bot_guild)
                    return
            except Exception as e:
                print(f"[Slash Play] Lavalink 直通搜尋略過 ({e})，無縫使用原生解析管線...")

        loop = asyncio.get_event_loop()

        is_spotify = bool(SPOTIFY_URL_REGEX.search(查詢))
        is_netease = bool(NETEASE_URL_REGEX.search(查詢) or "163cn.tv" in 查詢 or "163.lu" in 查詢 or "music.163.com" in 查詢)
        is_apple = bool(APPLE_MUSIC_URL_REGEX.search(查詢) or "apple.co" in 查詢 or "music.apple.com" in 查詢)

        if is_spotify or is_netease or is_apple:
            ext_res = None
            if is_spotify:
                ext_res = await resolve_spotify(查詢)
                if ext_res:
                    ext_res.setdefault('platform', 'spotify')
                    ext_res.setdefault('platform_name', 'Spotify')
                    ext_res.setdefault('color', 0x1DB954)
            elif is_netease:
                ext_res = await resolve_netease(查詢)
            elif is_apple:
                ext_res = await resolve_apple_music(查詢)

            if not ext_res or not ext_res.get('tracks'):
                platform_label = "Spotify" if is_spotify else ("網易雲音樂" if is_netease else "Apple Music")
                await interaction.followup.send(f"❌ 無法解析該 {platform_label} 連結，請確認該歌曲或播放清單為公開狀態！")
                return

            platform_name = ext_res.get('platform_name', '音樂平台')
            embed_color = ext_res.get('color', 0x1DB954)

            if ext_res.get('type') in ('playlist', 'album') or len(ext_res.get('tracks', [])) > 1:
                tracks = ext_res.get('tracks', [])
                playlist_title = ext_res.get('title', f"{platform_name} 播放清單") or f"{platform_name} 播放清單"
                playlist_thumb = ext_res.get('thumbnail', '') or ''
                added_count = 0

                for track_item in tracks[:200]:
                    if not track_item or not isinstance(track_item, dict):
                        continue
                    t_title = track_item.get('title', '未知歌曲') or '未知歌曲'
                    t_artist = track_item.get('artist', '') or ''
                    title_str = f"{t_title} - {t_artist}" if t_artist else t_title
                    search_q = f"ytsearch5:{t_title} {t_artist}".strip()
                    song = Song({
                        'title': title_str,
                        'webpage_url': track_item.get('url') or 查詢,
                        'url': search_q,
                        'duration': track_item.get('duration', 0) or 0,
                        'thumbnail': track_item.get('thumbnail') or playlist_thumb,
                        'uploader': t_artist or platform_name
                    }, interaction.user)
                    state.queue.append(song)
                    added_count += 1

                type_label = "播放清單" if ext_res.get('type') == 'playlist' else "專輯"
                embed = discord.Embed(
                    title=f"🎶 【{selected_bot.node_name}】成功匯入 {platform_name} {type_label}！",
                    description=f"**[{playlist_title}]({查詢})**",
                    color=embed_color
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
                return

            valid_tracks = [t for t in ext_res.get('tracks', []) if t and isinstance(t, dict)]
            if not valid_tracks:
                platform_label = "Spotify" if is_spotify else ("網易雲音樂" if is_netease else "Apple Music")
                await interaction.followup.send(f"❌ 無法解析該 {platform_label} 連結中的歌曲資訊！")
                return
            track_item = valid_tracks[0]
            t_title = track_item.get('title', '未知歌曲') or '未知歌曲'
            t_artist = track_item.get('artist', '') or ''
            title_str = f"{t_title} - {t_artist}" if t_artist else t_title
            search_q = f"ytsearch5:{t_title} {t_artist}".strip()

            yt_data = None
            try:
                data = await loop.run_in_executor(
                    MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(search_q, download=False)
                )
                if data and 'entries' in data and data['entries']:
                    for entry in data['entries']:
                        if entry and (entry.get('url') or entry.get('formats')):
                            yt_data = entry
                            break
                elif data and (data.get('url') or data.get('formats')):
                    yt_data = data
            except Exception:
                yt_data = None

            if not yt_data:
                try:
                    sc_q = f"scsearch3:{t_title} {t_artist}".strip()
                    data = await loop.run_in_executor(
                        MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(sc_q, download=False)
                    )
                    if data and 'entries' in data and data['entries']:
                        for entry in data['entries']:
                            if entry and (entry.get('url') or entry.get('formats')):
                                yt_data = entry
                                break
                    elif data and (data.get('url') or data.get('formats')):
                        yt_data = data
                except Exception:
                    yt_data = None

            if not yt_data:
                await interaction.followup.send(f"❌ 找不到該 {platform_name} 歌曲 **{title_str}** 的音源！")
                return

            yt_data['title'] = title_str
            yt_data['webpage_url'] = track_item.get('url') or 查詢
            if track_item.get('thumbnail'):
                yt_data['thumbnail'] = track_item['thumbnail']
            if track_item.get('duration'):
                yt_data['duration'] = track_item['duration']
            yt_data['uploader'] = t_artist or platform_name

            song = Song(yt_data, interaction.user)

            if is_vc_active(vc) and state.current:
                state.queue.append(song)
                embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
                await interaction.followup.send(embed=embed)
            else:
                state.queue.append(song)
                if not already_in_room:
                    await interaction.followup.send(t('song_dispatched', interaction, title=song.title, bot_name=selected_bot.node_name, channel=voice_channel.mention))
                else:
                    await interaction.followup.send(t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name))
                await play_next(selected_bot, bot_guild)
            return

        is_playlist = ("playlist" in 查詢.lower()) or ("list=" in 查詢.lower())

        if is_playlist:
            try:
                data = await loop.run_in_executor(
                    MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=True, noplaylist=False).extract_info(查詢, download=False)
                )
            except Exception as e:
                await interaction.followup.send(f"❌ 讀取播放清單失敗: `{e}`")
                return

            if not data or not isinstance(data, dict):
                await interaction.followup.send("❌ 讀取播放清單失敗，查無內容！")
                return

            entries = data.get('entries', []) if isinstance(data, dict) else []
            valid = [e for e in entries if e and isinstance(e, dict) and (e.get('title') or e.get('url'))]
            if not valid:
                await interaction.followup.send("❌ 該播放清單中未找到任何有效歌曲！")
                return

            added_count = 0
            playlist_title = data.get('title', '播放清單') or '播放清單'
            for e in valid[:200]:
                title = e.get('title', '未知歌曲') or '未知歌曲'
                if title in ['[Deleted video]', '[Private video]']:
                    continue
                vid = e.get('id')
                song_url = e.get('webpage_url')
                vid_str = str(vid) if vid is not None else ''
                if not song_url and vid_str and re.match(r'^[a-zA-Z0-9_-]{11}$', vid_str):
                    song_url = f"https://www.youtube.com/watch?v={vid_str}"
                elif not song_url:
                    raw_url = e.get('url', '') or ''
                    raw_str = str(raw_url) if raw_url is not None else ''
                    if raw_str.startswith('http'):
                        song_url = raw_str
                    elif vid_str and re.match(r'^[a-zA-Z0-9_-]{11}$', vid_str):
                        song_url = f"https://www.youtube.com/watch?v={vid_str}"
                    elif raw_str and re.match(r'^[a-zA-Z0-9_-]{11}$', raw_str):
                        song_url = f"https://www.youtube.com/watch?v={raw_str}"
                    else:
                        song_url = ''
                song = Song({
                    'id': vid,
                    'title': title,
                    'webpage_url': song_url,
                    'url': song_url,
                    'duration': e.get('duration', 0) or 0,
                    'thumbnail': e.get('thumbnail', '') or ''
                }, interaction.user)
                state.queue.append(song)
                added_count += 1

            embed = discord.Embed(
                title=f"📑 【{selected_bot.node_name}】成功匯入播放清單！",
                description=f"**[{playlist_title}]({查詢})**",
                color=0x2ECC71
            )
            embed.add_field(name="🔢 成功加入", value=f"`{added_count}` 首歌曲", inline=True)
            embed.add_field(name="📍 當前隊列長度", value=f"`{len(state.queue)}` 首", inline=True)
            embed.add_field(name="🤖 服務節點", value=f"`{selected_bot.node_name}`", inline=True)
            embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed)

            if not is_vc_active(vc):
                await play_next(selected_bot, bot_guild)
            return

        data = None
        try:
            data = await loop.run_in_executor(
                MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(查詢, download=False)
            )
        except Exception as e:
            try:
                sc_q = f"scsearch:{查詢}"
                data = await loop.run_in_executor(
                    MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(sc_q, download=False)
                )
            except Exception:
                await interaction.followup.send(f"❌ 找不到該歌曲或解析失敗: `{e}`")
                return

        if not data:
            try:
                sc_q = f"scsearch:{查詢}"
                data = await loop.run_in_executor(
                    MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=False, noplaylist=True).extract_info(sc_q, download=False)
                )
            except Exception:
                pass

        if not data or not isinstance(data, dict):
            await interaction.followup.send("❌ 找不到該歌曲或解析結果為空，請更換關鍵字！")
            return

        if 'entries' in data and data.get('entries') is not None:
            valid_entries = [e for e in data['entries'] if e and isinstance(e, dict)]
            if not valid_entries:
                await interaction.followup.send("❌ 搜尋結果為空，請更換關鍵字！")
                return
            data = valid_entries[0]

        if not data or not isinstance(data, dict):
            await interaction.followup.send("❌ 找不到有效歌曲資訊！")
            return

        song = Song(data, interaction.user)

        if is_vc_active(vc) and state.current:
            state.queue.append(song)
            embed = build_song_queued_embed(selected_bot, song, state, len(state.queue), interaction)
            await interaction.followup.send(embed=embed)
        else:
            state.queue.append(song)
            if not already_in_room:
                await interaction.followup.send(t('song_dispatched', interaction, title=song.title, bot_name=selected_bot.node_name, channel=voice_channel.mention))
            else:
                await interaction.followup.send(t('song_started', interaction, title=song.title, bot_name=selected_bot.node_name))
            await play_next(selected_bot, bot_guild)

    @slash_play.autocomplete("查詢")
    async def slash_play_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        state = None
        if NODE_BOTS and interaction.guild_id:
            state = NODE_BOTS[0].get_state(interaction.guild_id)

        choices = []
        cur_stripped = current.strip()

        if cur_stripped:
            now = time.time()
            cached_entry = _AUTOCOMPLETE_CACHE.get(cur_stripped)
            if cached_entry and (now - cached_entry[1] < 120.0):
                for s in cached_entry[0]:
                    choices.append(app_commands.Choice(name=f"🔍 {s[:95]}", value=s[:100]))
            else:
                try:
                    session = await get_http_session()
                    q_enc = urllib.parse.quote(cur_stripped)
                    api_url = f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={q_enc}"
                    async with session.get(api_url, timeout=1.0) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
                                raw_suggestions = []
                                for s in data[1][:7]:
                                    if s and s not in raw_suggestions:
                                        raw_suggestions.append(s)
                                        choices.append(app_commands.Choice(name=f"🔍 {s[:95]}", value=s[:100]))
                                _AUTOCOMPLETE_CACHE[cur_stripped] = (raw_suggestions, now)
                                if len(_AUTOCOMPLETE_CACHE) > 500:
                                    oldest_keys = sorted(_AUTOCOMPLETE_CACHE.keys(), key=lambda k: _AUTOCOMPLETE_CACHE[k][1])[:100]
                                    for ok in oldest_keys:
                                        _AUTOCOMPLETE_CACHE.pop(ok, None)
                except Exception:
                    pass

        if state and state.history:
            seen = set()
            for song in state.history:
                if song.title and song.title not in seen:
                    seen.add(song.title)
                    if not cur_stripped or cur_stripped.lower() in song.title.lower():
                        if song.title not in [c.value for c in choices]:
                            choices.append(app_commands.Choice(name=f"🕒 {song.title[:95]}", value=song.title[:100]))
                            if len(choices) >= 10:
                                break

        if not cur_stripped and not choices:
            default_suggestions = [
                "周杰倫 經典熱門歌曲",
                "Lofi Hip Hop chill beats",
                "Trending Pop Music 2026",
                "華語流行音樂排行榜",
                "Billboard Hot 100"
            ]
            for s in default_suggestions:
                choices.append(app_commands.Choice(name=f"🔥 {s}", value=s))

        if cur_stripped and not choices:
            choices.append(app_commands.Choice(name=f"🔍 {cur_stripped[:95]}", value=cur_stripped[:100]))

        return choices[:10]

    @tree.command(name="search", description="搜尋歌曲並從前 5 個結果中挑選播放 (Search & pick from top 5)")
    @app_commands.describe(關鍵字="要搜尋的歌曲名稱或歌手")
    async def slash_search(interaction: discord.Interaction, 關鍵字: str):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        await interaction.response.defer()
        selected_bot, is_new_join = await find_best_bot_for_channel(interaction.guild_id, voice_channel, interaction.guild)
        if not selected_bot:
            await interaction.followup.send(t('no_bot_available', interaction))
            return

        bot_guild = get_bot_guild(selected_bot, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(selected_bot, 'voice_clients', []), guild__id=interaction.guild_id)

        if is_new_join or not vc or not is_vc_connected(vc):
            try:
                vc = await safe_voice_connect(selected_bot, voice_channel, timeout=20.0)
            except Exception as e:
                await interaction.followup.send(t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e)))
                return

        loop = asyncio.get_event_loop()
        query = f"ytsearch5:{關鍵字}"
        try:
            data = await loop.run_in_executor(
                MEDIA_EXECUTOR, lambda: get_thread_ytdl(extract_flat=True, noplaylist=True).extract_info(query, download=False)
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 搜尋失敗: `{e}`")
            return

        entries = [e for e in (data.get('entries', []) if data and isinstance(data, dict) else []) if e and isinstance(e, dict) and e.get('title')][:5]
        if not entries:
            await interaction.followup.send(f"❌ 找不到與 `{關鍵字}` 相關的搜尋結果！")
            return

        embed = discord.Embed(
            title=f"🔍 【{selected_bot.node_name}】搜尋結果 — 請選擇歌曲",
            description=f"關鍵字：`{關鍵字}`\n請於下方選單點選您欲點播的歌曲：",
            color=0x5865F2
        )
        for i, item in enumerate(entries):
            dur = format_duration(item.get('duration', 0))
            embed.add_field(
                name=f"{i+1}. {item.get('title', '未知')[:60]}",
                value=f"⏱️ 時長: `{dur}` | 👤 上傳者: `{item.get('uploader', '未知')[:30]}`",
                inline=False
            )

        view = SearchSelectView(selected_bot, bot_guild, interaction.user, entries)
        await interaction.followup.send(embed=embed, view=view)

    @slash_search.autocomplete("關鍵字")
    async def slash_search_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return await slash_play_autocomplete(interaction, current)

    @tree.command(name="skip", description="跳過你當前語音房的機器人歌曲")
    async def slash_skip(interaction: discord.Interaction):
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
            await interaction.response.send_message(f"❌ 該指令目前僅限擁有 DJ 身分組 <@&{state.dj_role_id}> 或管理員使用！", ephemeral=True)
            return

        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if vc and is_vc_active(vc):
            if state.loop_mode == 'single':
                state.loop_mode = 'off'
            await safe_stop(vc)
            await interaction.response.send_message(t('skipped', interaction, bot_name=b.node_name))
        else:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)

    @tree.command(name="stop", description="停止音樂機器人並讓其離開語音房")
    @app_commands.describe(全部退出="是否強制讓本伺服器所有音樂機器人全數停止並退出語音房？")
    async def slash_stop(interaction: discord.Interaction, 全部退出: bool = False):
        guild_id = interaction.guild_id
        if 全部退出:
            if not (hasattr(interaction.user, 'guild_permissions') and interaction.user.guild_permissions.administrator):
                await interaction.response.send_message("❌ 強制全體退出指令僅限伺服器管理員使用！", ephemeral=True)
                return
            stopped_names = []
            for b in NODE_BOTS:
                bg = get_bot_guild(b, guild_id, interaction.guild)
                if bg:
                    b_vc = bg.voice_client or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=guild_id)
                    if b_vc and is_vc_connected(b_vc):
                        b_state = b.get_state(guild_id)
                        b_state.loop_mode = 'off'
                        b_state.current = None
                        b_state.queue.clear()
                        b_state.is_playing = False
                        await safe_stop(b_vc)
                        await safe_disconnect(b_vc)
                        stopped_names.append(b.node_name)
            if stopped_names:
                await interaction.response.send_message(t('force_stopped_all', interaction, names=', '.join(stopped_names)))
            else:
                await interaction.response.send_message(t('no_bots_connected', interaction), ephemeral=True)
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
            await interaction.response.send_message(f"❌ 該指令目前僅限擁有 DJ 身分組 <@&{state.dj_role_id}> 或管理員使用！", ephemeral=True)
            return

        state.loop_mode = 'off'
        state.current = None
        state.queue.clear()
        state.is_playing = False
        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if vc:
            await safe_stop(vc)
            await safe_disconnect(vc)
        await interaction.response.send_message(t('stopped_and_left', interaction, bot_name=b.node_name))

    @tree.command(name="nowplaying", description="查看你所在語音房當前歌曲詳情與控制器")
    async def slash_nowplaying(interaction: discord.Interaction):
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
                pass
        view = MusicControllerView(b, state)
        state.current_view = view
        await interaction.response.send_message(embed=embed, view=view)

    @tree.command(name="queue", description="查看你所在語音房的待播歌單")
    async def slash_queue(interaction: discord.Interaction):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        embed = build_queue_embed(b, state, lang=resolve_locale(interaction))
        await interaction.response.send_message(embed=embed)

    @tree.command(name="move", description="調整待播清單中指定歌曲的播放順序 (Move song position in queue)")
    @app_commands.describe(來源序號="要移動的歌曲在待播清單中的序號", 目標序號="要移至的新序號位置")
    async def slash_move(interaction: discord.Interaction, 來源序號: int, 目標序號: int):
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
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=state.dj_role_id), ephemeral=True)
            return

        q_len = len(state.queue)
        if q_len == 0:
            await interaction.response.send_message(t('queue_empty', interaction), ephemeral=True)
            return

        if 來源序號 < 1 or 來源序號 > q_len or 目標序號 < 1 or 目標序號 > q_len:
            await interaction.response.send_message(t('move_invalid_range', interaction, max_idx=q_len), ephemeral=True)
            return

        if 來源序號 == 目標序號:
            await interaction.response.send_message(t('move_invalid_range', interaction, max_idx=q_len), ephemeral=True)
            return

        queue_list = list(state.queue)
        target_song = queue_list.pop(來源序號 - 1)
        queue_list.insert(目標序號 - 1, target_song)
        state.queue = deque(queue_list)
        state.last_active = time.time()

        embed = discord.Embed(
            title="🔀 隊列順序調整成功！",
            description=t('move_success', interaction, src=來源序號, dst=目標序號) + f"\n**[{target_song.title}]({target_song.url})**",
            color=0x3498DB
        )
        await interaction.response.send_message(embed=embed)

    @tree.command(name="replay", description="重播歷史紀錄中的歌曲 (Replay a song from history)")
    @app_commands.describe(序號="歷史紀錄中的歌曲序號 (預設為 1，即上一首)")
    async def slash_replay(interaction: discord.Interaction, 序號: int = 1):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
            return

        selected_bot, is_new_join = await find_best_bot_for_channel(interaction.guild_id, voice_channel, interaction.guild)
        if not selected_bot:
            await interaction.response.send_message(t('no_bot_available', interaction), ephemeral=True)
            return

        state = selected_bot.get_state(interaction.guild_id)
        bot_guild = get_bot_guild(selected_bot, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(selected_bot, 'voice_clients', []), guild__id=interaction.guild_id)

        if not state.history:
            await interaction.response.send_message(t('history_empty', interaction), ephemeral=True)
            return

        h_len = len(state.history)
        if 序號 < 1 or 序號 > h_len:
            await interaction.response.send_message(t('replay_invalid_index', interaction), ephemeral=True)
            return

        history_song = state.history[序號 - 1]
        new_song = Song(history_song.data, requester=interaction.user, wavelink_track=history_song.wavelink_track)
        new_song.stream_url = history_song.stream_url
        new_song.duration = history_song.duration
        new_song.thumbnail = history_song.thumbnail

        if is_new_join or not vc or not is_vc_connected(vc):
            try:
                vc = await safe_voice_connect(selected_bot, voice_channel, timeout=20.0)
            except Exception as e:
                await interaction.response.send_message(t('connect_failed', interaction, bot_name=selected_bot.node_name, error=str(e)), ephemeral=True)
                return

        state.text_channel = interaction.channel
        state.queue.append(new_song)

        embed = discord.Embed(
            title=f"🔄 【{selected_bot.node_name}】重新加入播放隊列！",
            description=f"**[{new_song.title}]({new_song.url})**",
            color=0x2ECC71
        )
        embed.add_field(name="⏱️ 時長", value=f"`{format_duration(new_song.duration)}`", inline=True)
        embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
        embed.add_field(name="📍 隊列位置", value=f"`第 {len(state.queue)} 位`", inline=True)
        if new_song.thumbnail:
            embed.set_thumbnail(url=new_song.thumbnail)
        await interaction.response.send_message(embed=embed)

        if not is_vc_active(vc):
            await play_next(selected_bot, bot_guild)
