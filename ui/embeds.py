from typing import TYPE_CHECKING, Optional, Tuple

import discord

from config import BASSBOOST_PRESETS, EQ_PRESETS, __version__, format_duration
from core.models import GuildMusicState, Song, get_playback_elapsed
from i18n import resolve_locale, t
from lyrics import get_synced_lyrics_snippet

if TYPE_CHECKING:
    from core.node import MusicNodeBot


def make_progress_bar(elapsed: int, total: int, length: int = 14) -> str:
    if not total or total <= 0:
        return "🔴 `LIVE 直播音訊`"
    percent = min(1.0, max(0.0, elapsed / total))
    pos = int(percent * length)
    pos = min(pos, length - 1)
    bar = "━" * pos + "🔘" + "─" * (length - 1 - pos)
    return f"`{format_duration(elapsed)}` {bar} `{format_duration(total)}`"


def get_platform_badge(url: str, uploader: str) -> Tuple[str, int]:
    u = (url or "").lower()
    up = (uploader or "").lower()
    if "spotify" in u or "spotify" in up:
        return "Spotify", 0x1DB954
    elif "163.com" in u or "163cn" in u or "163.lu" in u or "netease" in up or "網易" in up:
        return "網易雲音樂", 0xC20C0C
    elif "apple.com" in u or "apple.co" in u or "apple" in up:
        return "Apple Music", 0xFC3C44
    elif "soundcloud" in u:
        return "SoundCloud", 0xFF5500
    return "YouTube Music", 0xFF0000


def build_nowplaying_embed(bot: 'MusicNodeBot', song: Optional[Song], state: GuildMusicState, lang: Optional[str] = None) -> discord.Embed:
    guild_lang = lang or resolve_locale(guild_id=state.guild_id)
    target_song = song or state.current
    if not target_song:
        return discord.Embed(title=t('not_playing', lang=guild_lang), color=0x5865F2)

    loop_str = t(f'loop_{state.loop_mode}', lang=guild_lang)

    guild = bot.get_guild(state.guild_id)
    vc = (guild.voice_client if guild else None) or discord.utils.get(getattr(bot, 'voice_clients', []), guild__id=state.guild_id)
    bitrate_str = "128 kbps"
    ch = getattr(vc, 'channel', None) if vc else None
    ch_bitrate = getattr(ch, 'bitrate', None) if ch else None
    if ch_bitrate:
        bitrate_str = f"{ch_bitrate // 1000} kbps"

    bb_label = BASSBOOST_PRESETS.get(state.bassboost, {}).get("label", "關閉")
    eq_label = EQ_PRESETS.get(state.filter, {}).get("label", "原音直通")
    platform_name, embed_color = get_platform_badge(target_song.url, target_song.uploader)

    elapsed = get_playback_elapsed(state)
    dur = target_song.duration or 0
    progress_line = make_progress_bar(elapsed, dur, length=15)

    embed = discord.Embed(
        title=f"💿 正在播放 • {platform_name}",
        description=f"### [{target_song.title}]({target_song.url})\n👤 演出者: **{target_song.uploader or '未知演出者'}**\n\n{progress_line}",
        color=embed_color
    )
    if target_song.thumbnail:
        embed.set_thumbnail(url=target_song.thumbnail)

    if state.current_lyrics and state.current_lyrics[2]:
        snippet = get_synced_lyrics_snippet(state.current_lyrics[1], elapsed, context_lines=1)
        if snippet:
            embed.add_field(name="🎤 即時動態歌詞 (Synced Karaoke)", value=snippet, inline=False)

    req_mention = getattr(target_song.requester, 'mention', '未知')
    embed.add_field(name="👤 點播者", value=req_mention, inline=True)
    embed.add_field(name="🔊 音量", value=f"`{int(state.volume * 100)}%`", inline=True)
    embed.add_field(name="🔁 循環模式", value=f"`{loop_str}`", inline=True)
    embed.add_field(name="🎛️ 音訊引擎", value=f"`{state.active_engine}`", inline=True)
    embed.add_field(name="📻 自動接歌", value=f"`{'開啟' if state.autoplay else '關閉'}`", inline=True)
    embed.add_field(name="📜 待播隊列", value=f"`{len(state.queue)} 首歌曲`", inline=True)

    bot_name = bot.node_name
    embed.set_footer(
        text=f"DJ Donut v{__version__} • {bot_name} • 48kHz / {bitrate_str} • EQ: {eq_label} • 重低音: {bb_label}"
    )
    return embed


def build_queue_embed(bot: 'MusicNodeBot', state: GuildMusicState, lang: Optional[str] = None, page: int = 1, per_page: int = 10) -> discord.Embed:
    guild_lang = lang or resolve_locale(guild_id=state.guild_id)
    embed = discord.Embed(
        title=f"📜 【{bot.node_name}】待播清單與播放佇列",
        color=0x5865F2
    )
    if state.current:
        cur = state.current
        elapsed = get_playback_elapsed(state)
        dur = cur.duration or 0
        time_tag = f"`[{format_duration(elapsed)} / {format_duration(dur)}]`" if dur else "`[LIVE 直播]`"
        req_m = getattr(cur.requester, 'mention', '未知')
        embed.add_field(
            name="▶️ 正在播放 (Now Playing)",
            value=f"**[{cur.title}]({cur.url})**\n⏱️ {time_tag} • 👤 點播者: {req_m}",
            inline=False
        )
    else:
        embed.add_field(name="▶️ 正在播放 (Now Playing)", value="*目前無正在播放的歌曲*", inline=False)

    q_list = list(state.queue)
    total_items = len(q_list)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    cur_page = max(1, min(page, total_pages))

    if total_items == 0:
        embed.add_field(name="📋 待播歌曲清單", value="*待播佇列目前是空的，輸入 `/play` 點播歌曲吧！*", inline=False)
    else:
        start_idx = (cur_page - 1) * per_page
        end_idx = min(total_items, start_idx + per_page)
        total_seconds = sum((s.duration or 0) for s in q_list)
        total_dur_str = format_duration(total_seconds) if total_seconds else "未知"

        queue_lines = []
        for idx in range(start_idx, end_idx):
            s = q_list[idx]
            s_dur = f"`{format_duration(s.duration)}`" if s.duration else "`直播`"
            req_m = getattr(s.requester, 'mention', '未知')
            queue_lines.append(f"`{idx + 1}.` [{s.title[:48]}]({s.url}) — {s_dur} ({req_m})")

        header_meta = f"總計 `{total_items}` 首歌曲 • 預估總時長 `{total_dur_str}` • 第 `{cur_page}/{total_pages}` 頁\n\n"
        embed.add_field(
            name="📋 待播歌曲清單",
            value=header_meta + "\n".join(queue_lines),
            inline=False
        )

    loop_str = t(f'loop_{state.loop_mode}', lang=guild_lang)
    embed.set_footer(
        text=f"第 {cur_page}/{total_pages} 頁 • 🔁 循環: {loop_str} • 📻 自動接歌: {'開啟' if state.autoplay else '關閉'} • 🔊 音量: {int(state.volume * 100)}%"
    )
    return embed


def build_song_queued_embed(bot: 'MusicNodeBot', song: Song, state: GuildMusicState, pos: int, interaction: discord.Interaction) -> discord.Embed:
    platform_name, embed_color = get_platform_badge(song.url, song.uploader)
    dur_str = format_duration(song.duration, resolve_locale(interaction))
    embed = discord.Embed(
        title=f"✅ 已加入隊列 • {platform_name}",
        description=f"### [{song.title}]({song.url})\n👤 演出者: **{song.uploader or '未知'}**",
        color=embed_color
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    embed.add_field(name="⏱️ 歌曲時長", value=f"`{dur_str}`", inline=True)
    embed.add_field(name="📍 播放順位", value=f"`#{pos}`", inline=True)
    embed.add_field(name="🤖 服務節點", value=f"`{bot.node_name}`", inline=True)
    embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
    embed.add_field(name="📊 隊列總計", value=f"`{len(state.queue)} 首待播`", inline=True)
    embed.add_field(name="🔊 音量設定", value=f"`{int(state.volume * 100)}%`", inline=True)
    embed.set_footer(text="DJ Donut • 輸入 /nowplaying 查看控制器 • /queue 查看待播清單")
    return embed
