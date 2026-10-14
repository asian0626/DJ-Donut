import discord
from typing import Optional, Tuple, Any
from config import __version__, BASSBOOST_PRESETS, EQ_PRESETS, format_duration
from core.models import Song, GuildMusicState, get_playback_elapsed
from i18n import t, resolve_locale

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


def build_nowplaying_embed(bot: 'MusicNodeBot', song: Song, state: GuildMusicState, lang: Optional[str] = None) -> discord.Embed:
    guild_lang = lang or resolve_locale(guild_id=state.guild_id)
    loop_str = t(f'loop_{state.loop_mode}', lang=guild_lang)

    guild = bot.get_guild(state.guild_id)
    vc = guild.voice_client if guild else None
    bitrate_str = "128 kbps"
    if vc and hasattr(vc, 'channel') and vc.channel and hasattr(vc.channel, 'bitrate') and vc.channel.bitrate:
        bitrate_str = f"{vc.channel.bitrate // 1000} kbps"

    bb_label = BASSBOOST_PRESETS.get(state.bassboost, {}).get("label", "關閉")
    eq_label = EQ_PRESETS.get(state.filter, {}).get("label", "原音直通")
    platform_name, embed_color = get_platform_badge(song.url, song.uploader)

    elapsed = get_playback_elapsed(state)
    dur = song.duration or 0
    progress_line = make_progress_bar(elapsed, dur, length=15)

    embed = discord.Embed(
        title=f"💿 正在播放 • {platform_name}",
        description=f"### [{song.title}]({song.url})\n👤 演出者: **{song.uploader or '未知演出者'}**\n\n{progress_line}",
        color=embed_color
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    embed.add_field(name="👤 點播者", value=song.requester.mention, inline=True)
    embed.add_field(name="🔊 音量", value=f"`{int(state.volume * 100)}%`", inline=True)
    embed.add_field(name="🔁 循環模式", value=f"`{loop_str}`", inline=True)
    embed.add_field(name="🎛️ 音訊引擎", value=f"`{state.active_engine}`", inline=True)
    embed.add_field(name="📻 自動接歌", value=f"`{'開啟' if state.autoplay else '關閉'}`", inline=True)
    embed.add_field(name="📜 待播隊列", value=f"`{len(state.queue)} 首歌曲`", inline=True)

    bot_name = bot.node_name
    embed.set_footer(
        text=f"DJ Donut • {bot_name} • 48kHz / {bitrate_str} • EQ: {eq_label} • 重低音: {bb_label}"
    )
    return embed

def build_queue_embed(bot: 'MusicNodeBot', state: GuildMusicState, lang: Optional[str] = None) -> discord.Embed:
    guild_lang = lang or resolve_locale(guild_id=state.guild_id)
    embed = discord.Embed(
        title=f"📜 【{bot.node_name}】語音房待播清單",
        color=0x5865F2
    )
    if state.current:
        elapsed = get_playback_elapsed(state)
        dur = state.current.duration
        time_tag = f"`[{format_duration(elapsed)} / {format_duration(dur)}]`" if dur else "`[LIVE 直播]`"
        embed.description = f"### ▶️ 現正播放\n**[{state.current.title}]({state.current.url})**\n{time_tag} • 由 {state.current.requester.mention} 點播\n"
    else:
        embed.description = "⏸️ **目前沒有正在播放的歌曲**\n使用 `/play <歌曲或網址>` 即可開始點播！\n"

    if not state.queue:
        embed.add_field(
            name="📋 待播歌曲 (0)",
            value="*目前隊列為空，快使用 `/play` 點歌，或開啟 `/autoplay` 電台自動接歌！*",
            inline=False
        )
    else:
        total_seconds = sum(s.duration for s in state.queue if s.duration)
        total_dur_str = format_duration(total_seconds) if total_seconds else "未知"
        queue_lines = []
        for i, s in enumerate(list(state.queue)[:10], start=1):
            s_dur = f"`{format_duration(s.duration)}`" if s.duration else "`直播`"
            queue_lines.append(f"`{i:02d}.` **[{s.title[:35]}]({s.url})** ({s_dur}) • {s.requester.mention}")

        remaining = len(state.queue) - 10
        if remaining > 0:
            queue_lines.append(f"\n*...以及其他 **{remaining}** 首歌曲*")

        header_meta = f"總計 `{len(state.queue)}` 首歌曲 • 預估總時長 `{total_dur_str}`\n\n"
        embed.add_field(
            name="📋 待播歌曲清單",
            value=header_meta + "\n".join(queue_lines),
            inline=False
        )

    loop_str = t(f'loop_{state.loop_mode}', lang=guild_lang)
    embed.set_footer(
        text=f"🔁 循環: {loop_str} • 📻 自動接歌: {'開啟' if state.autoplay else '關閉'} • 🔊 音量: {int(state.volume * 100)}%"
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

