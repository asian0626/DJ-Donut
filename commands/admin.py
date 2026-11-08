import random
from collections import deque
from typing import Dict, Optional, Tuple
import discord
from discord import app_commands
from config import __version__, save_guild_setting
from i18n import t, GUILD_LANGUAGES
from lyrics import get_lyrics_async, format_clean_lyrics
from core.models import (
    is_vc_connected,
    is_vc_playing,
    has_dj_permission,
    get_playback_elapsed,
)
from core.node import NODE_BOTS, MusicNodeBot
from core.dispatcher import (
    get_bot_guild,
    get_bot_in_user_room,
    get_user_voice_channel,
)
from ui.embeds import format_duration

_INVITE_URLS: Dict[int, str] = {
    1: "https://discord.com/oauth2/authorize?client_id=1549028983641743462&permissions=2150648832&integration_type=0&scope=bot+applications.commands",
    2: "https://discord.com/oauth2/authorize?client_id=1549258600155979956&permissions=2150648832&integration_type=0&scope=bot+applications.commands",
    3: "https://discord.com/oauth2/authorize?client_id=1549259098761990184&permissions=2150648832&integration_type=0&scope=bot+applications.commands",
    4: "https://discord.com/oauth2/authorize?client_id=1549259563646062743&permissions=2150648832&integration_type=0&scope=bot+applications.commands",
    5: "https://discord.com/oauth2/authorize?client_id=1549259919696592976&permissions=2150648832&integration_type=0&scope=bot+applications.commands",
}


def _format_node_status_field(
    idx: int,
    b: MusicNodeBot,
    guild_id: Optional[int],
    interaction_guild: Optional[discord.Guild],
) -> Tuple[str, str]:
    guild = get_bot_guild(b, guild_id, interaction_guild)
    state = b.get_state(guild_id)
    g_vc = (guild.voice_client if guild else None) or discord.utils.get(
        getattr(b, 'voice_clients', []), guild__id=guild_id
    )

    if not guild:
        bot_id = b.application_id or (b.user.id if b.user else None)
        invite_link = (
            f"https://discord.com/oauth2/authorize?client_id={bot_id}&permissions=2150648832&integration_type=0&scope=bot+applications.commands"
            if bot_id
            else _INVITE_URLS.get(idx, "")
        )
        link_text = f"> 🔗 [點此邀請 {idx} 號機器人進群]({invite_link})" if invite_link else "> 尚未加入此伺服器"
        return f"⚪ 【{b.node_name}】未加入此伺服器", link_text

    if g_vc and is_vc_connected(g_vc):
        ch_obj = getattr(g_vc, 'channel', None)
        vc_name = str(getattr(ch_obj, 'name', 'Voice')) if ch_obj else "Voice"
        is_play = is_vc_playing(g_vc)
        cur_title = state.current.title if state.current else "未知歌曲"
        cur_url = state.current.url if state.current else ""
        cur_dur = format_duration(state.current.duration) if (state.current and state.current.duration) else "LIVE"
        elapsed = format_duration(get_playback_elapsed(state))
        status_badge = "🟢 正在播放" if is_play else "🟡 暫停中"
        song_link = f"**[{cur_title[:32]}]({cur_url})**" if cur_url else f"**{cur_title[:32]}**"
        return (
            f"{status_badge} • {b.node_name}",
            f"> 🔊 語音房間: **#{vc_name}**\n"
            f"> 🎵 播放歌曲: {song_link} `[{elapsed}/{cur_dur}]`\n"
            f"> 📊 待播隊列: `{len(state.queue)} 首` • 音量: `{int(state.volume * 100)}%`",
        )

    return (
        f"🟢 【{b.node_name}】空閒待命中",
        "> 💤 節點已就緒，於任意語音房輸入 `/play` 將自動指派進房！",
    )


async def _handle_status(interaction: discord.Interaction) -> None:
    guild_id = interaction.guild_id
    total_bots = len(NODE_BOTS)
    active_bots = 0
    playing_bots = 0

    for b in NODE_BOTS:
        bg = get_bot_guild(b, guild_id, interaction.guild)
        if bg:
            active_bots += 1
            g_vc = bg.voice_client or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=guild_id)
            if g_vc and is_vc_playing(g_vc):
                playing_bots += 1

    load_pct = int((playing_bots / total_bots) * 100) if total_bots else 0
    load_bar = "▰" * (playing_bots * 2) + "▱" * ((total_bots - playing_bots) * 2)

    embed = discord.Embed(
        title="🎛️ DJ Donut — 機器人矩陣調度儀表板",
        description=(
            f"**伺服器進駐節點: `{active_bots}/{total_bots}` 台**\n"
            f"📊 **叢集負載率: `{load_pct}%`** `[{load_bar}]`\n"
            f"⚡ 多房間獨立引擎: `48kHz Stereo / 384kbps VBR`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x5865F2,
    )

    for idx, b in enumerate(NODE_BOTS, start=1):
        field_name, field_val = _format_node_status_field(idx, b, guild_id, interaction.guild)
        embed.add_field(name=field_name, value=field_val, inline=False)

    embed.set_footer(text=f"DJ Donut v{__version__} | Multi-Room Music Matrix | 支援 5 房獨立播放")
    await interaction.response.send_message(embed=embed)


async def _handle_help(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="🎧 DJ Donut — 現代化音樂機器人使用指南",
        description=(
            "支援多房間無縫分流播放、高解析度進度條、即時動態歌詞與錄音室等化器。\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="🎵 音樂播放指令",
        value=(
            "> `/play <查詢>` — 智慧點歌（支援歷史搜尋自動補全）\n"
            "> `/search <關鍵字>` — 搜尋歌曲並由下拉選單精準挑選\n"
            "> `/nowplaying` — 開啟進度條與全功能互動控制器\n"
            "> `/queue` — 檢視當前語音房的待播歌單隊列\n"
            "> `/skip` — 跳過當前歌曲 (支援 DJ 權限)\n"
            "> `/stop` — 停止播放並讓機器人退出語音房"
        ),
        inline=False,
    )
    embed.add_field(
        name="📑 隊列進階管理與個人收藏",
        value=(
            "> `/favorite <動作>` — 管理與一鍵播放您的個人最愛收藏歌單\n"
            "> `/move <來源> <目標>` — 自由調整隊列歌曲播放順序\n"
            "> `/seek <時間>` — 精確跳轉當前歌曲播放進度 (如: 90 或 01:30)\n"
            "> `/shuffle` — 隨機打亂當前語音房的待播歌單隊列\n"
            "> `/remove <編號>` — 移除待播清單中的指定序號歌曲\n"
            "> `/clear` — 一鍵清空當前語音房所有的待播歌曲\n"
            "> `/history` — 檢視最近播放的 15 首歷史歌曲紀錄\n"
            "> `/replay [序號]` — 一鍵重新點播歷史紀錄中的歌曲"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ 權限與進階設定",
        value=(
            "> `/dj [身分組]` — 設定、檢視或清除本伺服器 DJ 控制權限\n"
            "> `/autoplay` — 開啟/關閉智慧自動接歌電台模式\n"
            "> `/lyrics [歌曲]` — 查詢即時動態時間軸歌詞與純文字歌詞\n"
            "> `/247` — 切換 24/7 永久常駐語音房模式"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎚️ 音質與錄音室等化器",
        value=(
            "> `/bassboost <強度>` — 即時調節 +4dB ~ +20dB 重低音\n"
            "> `/filter <預設>` — 套用專業濾鏡 (搖滾/流行/人聲/夜核/8D等)\n"
            "> `/bitrate [kbps]` — 查看或一鍵拉滿語音房最高音質 (至 384k)\n"
            "> `/engine` — 雙引擎架構即時運作狀態與音訊規格檢視"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌐 叢集與語言設定",
        value=(
            "> `/status` — 查看 1～5 號機器人獨立運作與分流儀表板\n"
            "> `/language <語系>` — 切換伺服器預設語言 (支援 6 國語系)"
        ),
        inline=False,
    )
    embed.set_footer(text=f"DJ Donut v{__version__} • 支援 Spotify / 網易雲 / Apple Music / YouTube")
    await interaction.response.send_message(embed=embed)


async def _handle_shuffle(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    if len(state.queue) < 2:
        await interaction.response.send_message(t('shuffle_need_more', interaction), ephemeral=True)
        return

    queue_list = list(state.queue)
    random.shuffle(queue_list)
    state.queue = deque(queue_list)
    await interaction.response.send_message(t('shuffle_success', interaction, count=len(state.queue)))


async def _handle_remove(interaction: discord.Interaction, index: int) -> None:
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

    if not state.queue:
        await interaction.response.send_message(t('queue_empty', interaction), ephemeral=True)
        return

    if index < 1 or index > len(state.queue):
        await interaction.response.send_message(
            t('remove_invalid_index', interaction, max_idx=len(state.queue)), ephemeral=True
        )
        return

    queue_list = list(state.queue)
    removed_song = queue_list.pop(index - 1)
    state.queue = deque(queue_list)
    await interaction.response.send_message(
        t('remove_success', interaction, index=index, title=removed_song.title)
    )


async def _handle_clear(interaction: discord.Interaction) -> None:
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

    count = len(state.queue)
    if count == 0:
        await interaction.response.send_message(t('queue_empty', interaction), ephemeral=True)
        return

    state.queue.clear()
    await interaction.response.send_message(t('clear_success', interaction, count=count))


async def _handle_history(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    if not state.history:
        await interaction.response.send_message(t('history_empty', interaction), ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📜 【{b.node_name}】最近播放歷史 (共 {len(state.history)} 首)",
        color=0x3498DB,
    )
    history_list = list(state.history)
    history_list.reverse()
    desc_lines = []
    for i, s in enumerate(history_list[:15], 1):
        dur = format_duration(s.duration)
        song_link = f"[{s.title[:45]}]({s.url})" if s.url else s.title[:45]
        req_id = getattr(s.requester, 'id', None) if s.requester else None
        req_str = f" • <@{req_id}>" if req_id else ""
        desc_lines.append(f"`{i}.` **{song_link}** `[{dur}]`{req_str}")

    embed.description = "\n".join(desc_lines)
    embed.set_footer(text=f"DJ Donut v{__version__} | 顯示最近 15 首紀錄")
    await interaction.response.send_message(embed=embed)


def _is_guild_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, 'guild_permissions', None)
    return bool(perms and getattr(perms, 'administrator', False))


async def _handle_dj(
    interaction: discord.Interaction,
    role: Optional[discord.Role] = None,
    clear: bool = False,
) -> None:
    if not interaction.guild or not interaction.guild_id:
        await interaction.response.send_message("❌ 此指令僅限在伺服器中使用！", ephemeral=True)
        return

    state = NODE_BOTS[0].get_state(interaction.guild_id) if NODE_BOTS else None
    if not state:
        await interaction.response.send_message("❌ 機器人狀態未就緒！", ephemeral=True)
        return

    if clear:
        if not _is_guild_admin(interaction):
            await interaction.response.send_message("❌ 僅有伺服器管理員可清除 DJ 身分組設定！", ephemeral=True)
            return
        for b in NODE_BOTS:
            b.get_state(interaction.guild_id).dj_role_id = None
        save_guild_setting(interaction.guild_id, "dj_role_id", None)
        await interaction.response.send_message(t('dj_role_removed', interaction))
        return

    if role:
        if not _is_guild_admin(interaction):
            await interaction.response.send_message("❌ 僅有伺服器管理員可設定 DJ 身分組！", ephemeral=True)
            return
        for b in NODE_BOTS:
            b.get_state(interaction.guild_id).dj_role_id = role.id
        save_guild_setting(interaction.guild_id, "dj_role_id", role.id)
        await interaction.response.send_message(t('dj_role_set', interaction, role=role.mention))
        return

    if state.dj_role_id:
        await interaction.response.send_message(
            f"🎧 當前伺服器設定的 DJ 身分組為：<@&{state.dj_role_id}>\n"
            f"若要更改請使用 `/dj 身分組: @角色`，若要解除限制請使用 `/dj 清除: 是`。"
        )
    else:
        await interaction.response.send_message(
            "🎧 本伺服器目前**未設定** DJ 身分組限制（所有成員皆可自由切歌與操作）。\n"
            "管理員可透過 `/dj 身分組: @角色` 啟用 DJ 控制權限。"
        )


async def _handle_autoplay(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel or not interaction.guild_id:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    state.autoplay = not state.autoplay
    save_guild_setting(interaction.guild_id, "autoplay", state.autoplay)
    msg_key = 'autoplay_enabled' if state.autoplay else 'autoplay_disabled'
    await interaction.response.send_message(t(msg_key, interaction, bot_name=b.node_name))


async def _handle_247(interaction: discord.Interaction) -> None:
    voice_channel = await get_user_voice_channel(interaction)
    if not voice_channel or not interaction.guild_id:
        await interaction.response.send_message(t('must_join_voice', interaction), ephemeral=True)
        return

    b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
    if not b:
        await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
        return

    state = b.get_state(interaction.guild_id)
    state.mode_247 = not state.mode_247
    save_guild_setting(interaction.guild_id, "mode_247", state.mode_247)
    msg_key = 'mode_247_enabled' if state.mode_247 else 'mode_247_disabled'
    await interaction.response.send_message(t(msg_key, interaction, bot_name=b.node_name))


async def _handle_lyrics(interaction: discord.Interaction, song: Optional[str] = None) -> None:
    await interaction.response.defer()
    target_title = song
    target_artist = ""

    cur_elapsed: Optional[float] = None
    if not target_title:
        voice_channel = await get_user_voice_channel(interaction)
        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild) if voice_channel else None
        if b:
            state = b.get_state(interaction.guild_id)
            if state.current:
                target_title = state.current.title
                target_artist = state.current.uploader or ""
                cur_elapsed = float(get_playback_elapsed(state))

    if not target_title:
        await interaction.followup.send(t('not_playing', interaction))
        return

    res = await get_lyrics_async(target_title, target_artist)
    if not res:
        await interaction.followup.send(t('lyrics_not_found', interaction, title=target_title))
        return

    display_title, lrc_text, is_synced = res
    clean_body = format_clean_lyrics(lrc_text, is_synced, elapsed_sec=cur_elapsed)
    if len(clean_body) > 4000:
        clean_body = clean_body[:3990] + "\n..."

    embed = discord.Embed(
        title=t('lyrics_title', interaction, title=display_title),
        description=clean_body,
        color=0x1DB954 if is_synced else 0x3498DB,
    )
    sync_str = "⚡ 動態時間軸高亮 (Synced Karaoke)" if is_synced else "📝 純文字歌詞 (Plain)"
    embed.set_footer(text=f"模式: {sync_str} | 資料庫: LRCLIB / Netease")
    await interaction.followup.send(embed=embed)


async def _handle_language(interaction: discord.Interaction, language: app_commands.Choice[str]) -> None:
    guild_id = interaction.guild_id
    if not guild_id:
        await interaction.response.send_message("❌ 此指令僅能在伺服器頻道中使用！", ephemeral=True)
        return

    GUILD_LANGUAGES[guild_id] = language.value
    save_guild_setting(guild_id, "lang", language.value)
    for b in NODE_BOTS:
        state = b.get_state(guild_id)
        state.lang = language.value

    await interaction.response.send_message(
        t('language_updated', interaction, guild_id=guild_id, lang=language.value, lang_name=language.name)
    )


def _register_queue_admin_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="shuffle", description="隨機打亂當前語音房的待播歌單隊列")
    async def slash_shuffle(interaction: discord.Interaction) -> None:
        await _handle_shuffle(interaction)

    @tree.command(name="remove", description="從待播清單中移除指定序號的歌曲")
    @app_commands.describe(index="要移除的歌曲序號 (從 1 開始，可由 /queue 查詢)")
    @app_commands.rename(index="編號")
    async def slash_remove(interaction: discord.Interaction, index: int) -> None:
        await _handle_remove(interaction, index)

    @tree.command(name="clear", description="一鍵清空當前語音房所有的待播歌曲隊列")
    async def slash_clear(interaction: discord.Interaction) -> None:
        await _handle_clear(interaction)

    @tree.command(name="history", description="檢視當前語音房最近播放過的歷史歌曲紀錄")
    async def slash_history(interaction: discord.Interaction) -> None:
        await _handle_history(interaction)


def register_admin_commands(tree: app_commands.CommandTree) -> None:
    @tree.command(name="status", description="查看所有音樂機器人的即時連線、分流與伺服器進駐狀態儀表板")
    async def slash_status(interaction: discord.Interaction) -> None:
        await _handle_status(interaction)

    @tree.command(name="help", description="查看 DJ Donut 現代化多房間音樂機器人使用指南與指令清單")
    async def slash_help(interaction: discord.Interaction) -> None:
        await _handle_help(interaction)

    _register_queue_admin_commands(tree)

    @tree.command(name="dj", description="設定、檢視或清除本伺服器的 DJ 管理身分組 (Set or manage DJ role)")
    @app_commands.describe(role="要指定為 DJ 的身分組 (留空為檢視當前設定)", clear="是否清除 DJ 身分組限制")
    @app_commands.rename(role="身分組", clear="清除")
    async def slash_dj(
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
        clear: bool = False,
    ) -> None:
        await _handle_dj(interaction, role, clear)

    @tree.command(name="autoplay", description="開啟或關閉智慧自動接歌電台模式 (Toggle Autoplay radio mode)")
    async def slash_autoplay(interaction: discord.Interaction) -> None:
        await _handle_autoplay(interaction)

    @tree.command(name="247", description="切換機器人 24/7 永久常駐模式 (Toggle 24/7 permanence mode)")
    async def slash_247(interaction: discord.Interaction) -> None:
        await _handle_247(interaction)

    @tree.command(name="lyrics", description="查詢當前或指定歌曲的即時動態歌詞 (Synchronized / plain lyrics)")
    @app_commands.describe(song="可選：手動輸入要搜尋的歌曲名稱 (留空則搜尋當前播放中的歌曲)")
    @app_commands.rename(song="歌曲")
    async def slash_lyrics(interaction: discord.Interaction, song: Optional[str] = None) -> None:
        await _handle_lyrics(interaction, song)

    @tree.command(name="language", description="設定伺服器預設語言 (Set default language for this server)")
    @app_commands.describe(language="選擇偏好的顯示語言 (Choose preferred language)")
    @app_commands.choices(language=[
        app_commands.Choice(name="繁體中文 🇹🇼", value="zh-TW"),
        app_commands.Choice(name="English 🇺🇸", value="en"),
        app_commands.Choice(name="简体中文 🇨🇳", value="zh-CN"),
        app_commands.Choice(name="日本語 🇯🇵", value="ja"),
        app_commands.Choice(name="한국어 🇰🇷", value="ko"),
        app_commands.Choice(name="Español 🇪🇸", value="es"),
    ])
    @app_commands.rename(language="語言")
    async def slash_language(interaction: discord.Interaction, language: app_commands.Choice[str]) -> None:
        await _handle_language(interaction, language)
