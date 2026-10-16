import asyncio
import time
import subprocess
from typing import Optional
import discord
from discord import app_commands
from config import (
    MEDIA_EXECUTOR,
    HAS_WAVELINK,
    BASSBOOST_PRESETS,
    LAVALINK_URI,
    save_guild_setting,
    get_ffmpeg_options
)
from i18n import t
from core.models import (
    BufferedAudioSource,
    is_vc_connected,
    is_vc_playing,
    is_vc_paused
)
from core.node import NODE_BOTS
from core.dispatcher import (
    get_bot_guild,
    get_bot_in_user_room,
    get_user_voice_channel
)
from core.player import (
    play_next,
    safe_stop
)
from platforms.youtube import extract_stream_url_sync
from ui.embeds import (
    format_duration,
    parse_time_str
)

def register_audio_commands(tree: app_commands.CommandTree):
    @tree.command(name="seek", description="跳轉至當前播放歌曲的指定時間點 (例如: 90 或 01:30)")
    @app_commands.describe(時間="跳轉時間，格式可為秒數 (如 90) 或 分:秒 (如 01:30)")
    async def slash_seek(interaction: discord.Interaction, 時間: str):
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

        target_sec = parse_time_str(時間)
        if target_sec is None or target_sec < 0:
            await interaction.response.send_message(t('seek_invalid_time', interaction), ephemeral=True)
            return

        if state.current.duration and target_sec > state.current.duration:
            await interaction.response.send_message(t('seek_exceed_duration', interaction, duration=format_duration(state.current.duration)), ephemeral=True)
            return

        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if not vc or not is_vc_connected(vc):
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return

        if b.lavalink_connected and HAS_WAVELINK and hasattr(vc, 'seek'):
            try:
                await vc.seek(target_sec * 1000)
                state.start_time = time.time() - target_sec
                state.paused_duration = 0.0
                state.pause_timestamp = 0.0
                await interaction.response.send_message(t('seek_success', interaction, time_str=format_duration(target_sec)))
                return
            except Exception as e:
                logger.error(f"[{b.node_name}] Lavalink Seek Error: {e}")

        await interaction.response.defer()
        state.is_seeking = True
        stream_url = getattr(state.current, 'stream_url', '')
        if not stream_url or not stream_url.startswith('http'):
            loop = asyncio.get_event_loop()
            stream_url, _, _ = await loop.run_in_executor(
                MEDIA_EXECUTOR, lambda: extract_stream_url_sync(state.current.url, state.current.title)
            )
        if not stream_url:
            state.is_seeking = False
            await interaction.followup.send("❌ 無法提取音訊串流以執行跳轉！", ephemeral=True)
            return

        state.current.stream_url = stream_url
        if is_vc_playing(vc) or is_vc_paused(vc):
            await safe_stop(vc)

        try:
            ffmpeg_opts = get_ffmpeg_options(state, seek_seconds=target_sec)
            source = discord.FFmpegPCMAudio(stream_url, stderr=subprocess.DEVNULL, **ffmpeg_opts)
            buffered_source = BufferedAudioSource(source, prefetch_frames=50, max_frames=500)
            audio_source = discord.PCMVolumeTransformer(buffered_source, volume=state.volume)

            def after_callback(error):
                if state.is_seeking:
                    return
                if error:
                    logger.error(f"[{b.node_name} Player Error] {error}")
                asyncio.run_coroutine_threadsafe(play_next(b, bot_guild), b.loop)

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

            vc.play(audio_source, after=after_callback)
            state.start_time = time.time() - target_sec
            state.paused_duration = 0.0
            state.pause_timestamp = 0.0
            state.is_seeking = False
            await interaction.followup.send(t('seek_success', interaction, time_str=format_duration(target_sec)))
        except Exception as e:
            state.is_seeking = False
            await interaction.followup.send(f"❌ 跳轉執行失敗: `{e}`", ephemeral=True)

    @tree.command(name="bitrate", description="查看或一鍵將當前語音房提升至伺服器最高音質位元率 (96k ~ 384k)")
    @app_commands.describe(指定位元率="可選：手動指定 kbps 數值 (例如 96, 128, 256, 384)；留空則自動升至伺服器最高上限")
    async def slash_bitrate(interaction: discord.Interaction, 指定位元率: Optional[int] = None):
        channel = await get_user_voice_channel(interaction)
        if not channel:
            await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
            return
        guild = interaction.guild
        max_limit_bps = guild.bitrate_limit
        max_limit_kbps = max_limit_bps // 1000
        cur_kbps = channel.bitrate // 1000

        target_kbps = 指定位元率 if 指定位元率 is not None else max_limit_kbps
        target_bps = target_kbps * 1000

        if target_bps > max_limit_bps:
            await interaction.response.send_message(
                f"⚠️ 此伺服器等級最高支援 `{max_limit_kbps} kbps`！將為您調整為 `{max_limit_kbps} kbps`。",
                ephemeral=True
            )
            target_bps = max_limit_bps
            target_kbps = max_limit_kbps

        try:
            await channel.edit(bitrate=target_bps)
            embed = discord.Embed(
                title="🎚️ 語音房音質位元率已更新",
                color=0x2ECC71
            )
            embed.add_field(name="📍 語音頻道", value=channel.mention, inline=True)
            embed.add_field(name="📶 當前音質", value=f"`{target_kbps} kbps` (原: `{cur_kbps} kbps`)", inline=True)
            embed.add_field(name="🚀 伺服器上限", value=f"`{max_limit_kbps} kbps`", inline=True)
            embed.set_footer(text="💡 提示：聆聽音樂時，請關閉個人 Discord 設定的「Krisp 降噪」避免背景樂器被過濾！")
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="⚠️ 機器人缺少「管理頻道」權限",
                description=(
                    f"目前語音頻道 **{channel.name}** 的位元率為 `{cur_kbps} kbps`。\n\n"
                    f"**如何手動開啟極致音質？**\n"
                    f"1. 請伺服器管理員右鍵點擊頻道 -> **編輯頻道** -> **概覽**\n"
                    f"2. 將「**位元率**」滑桿直接拉至最右側最高 `{max_limit_kbps} kbps`\n"
                    f"3. 或在伺服器身分組中給予機器人「**管理頻道**」權限，機器人即可自動替您拉滿！"
                ),
                color=0xE67E22
            )
            embed.set_footer(text="💡 聽歌時請同時關閉個人 Discord「Krisp 降噪」以獲得最佳立體聲效果！")
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ 調整失敗: `{e}`", ephemeral=True)

    @tree.command(name="bassboost", description="即時調整或開啟重低音增強 (Bass Boost)")
    @app_commands.describe(等級="選擇重低音增強強度")
    @app_commands.choices(等級=[
        app_commands.Choice(name="❌ 關閉重低音", value="off"),
        app_commands.Choice(name="🍃 輕微低音 (+4dB)", value="low"),
        app_commands.Choice(name="🔊 飽滿重低音 (+8dB)", value="medium"),
        app_commands.Choice(name="💥 狂暴重低音 (+14dB)", value="high"),
        app_commands.Choice(name="🔥 震動全場 / 極致重低音 (+20dB)", value="extreme")
    ])
    async def slash_bassboost(interaction: discord.Interaction, 等級: app_commands.Choice[str]):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message("❌ 你所在的語音房內目前沒有任何音樂機器人！", ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.bassboost = 等級.value
        save_guild_setting(interaction.guild_id, "bassboost", 等級.value)
        choice_label = 等級.name

        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if b.lavalink_connected and HAS_WAVELINK and vc and hasattr(vc, 'filters'):
            try:
                filters = vc.filters
                bb_gain = BASSBOOST_PRESETS.get(state.bassboost, {}).get("gain", 0.0)
                if bb_gain > 0:
                    filters.equalizer.set(bands=[
                        {"band": 0, "gain": bb_gain},
                        {"band": 1, "gain": bb_gain * 0.8},
                        {"band": 2, "gain": bb_gain * 0.5},
                        {"band": 3, "gain": bb_gain * 0.2}
                    ])
                else:
                    filters.equalizer.reset()
                await vc.set_filters(filters)
                await interaction.response.send_message(f"⚡ 【{b.node_name}】Lavalink 32-bit DSP 重低音已**毫秒級即時生效**：**{choice_label}**！")
                return
            except Exception as e:
                print(f"[Bassboost Error] {e}")

        await interaction.response.send_message(f"🎛️ 【{b.node_name}】重低音已設為：**{choice_label}**！\n*(極限原生 FFmpeg 引擎將於切換或下首歌曲以 384k 精度套用)*")

    @tree.command(name="filter", description="套用專業等化器音效濾鏡 (搖滾/流行/人聲/高音/Nightcore/8D等)")
    @app_commands.describe(音效="選擇要套用的音效預設")
    @app_commands.choices(音效=[
        app_commands.Choice(name="🎵 原音直通 (重置所有濾波)", value="reset"),
        app_commands.Choice(name="🔊 重低音強化 (Bass Boost)", value="bass"),
        app_commands.Choice(name="🎸 搖滾動態 (V型強化)", value="rock"),
        app_commands.Choice(name="🎤 流行樂 (人聲中頻溫暖)", value="pop"),
        app_commands.Choice(name="✨ 清澈人聲 (凸顯主唱與細節)", value="vocal"),
        app_commands.Choice(name="💎 通透高音 (空靈清脆感)", value="treble"),
        app_commands.Choice(name="⚡ 夜核 Nightcore (加速升調)", value="nightcore"),
        app_commands.Choice(name="🌊 蒸氣波 Vaporwave (慢速復古)", value="vaporwave"),
        app_commands.Choice(name="🎧 8D 空間環繞立體聲", value="8d")
    ])
    async def slash_filter(interaction: discord.Interaction, 音效: app_commands.Choice[str]):
        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.response.send_message("❌ 你必須先加入一個語音頻道！", ephemeral=True)
            return

        b = get_bot_in_user_room(interaction.guild_id, voice_channel, interaction.guild)
        if not b:
            await interaction.response.send_message("❌ 你所在的語音房內目前沒有任何音樂機器人！", ephemeral=True)
            return

        state = b.get_state(interaction.guild_id)
        state.filter = 音效.value
        save_guild_setting(interaction.guild_id, "filter", 音效.value)
        choice_label = 音效.name

        bot_guild = get_bot_guild(b, interaction.guild_id, interaction.guild) or interaction.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(b, 'voice_clients', []), guild__id=interaction.guild_id)
        if b.lavalink_connected and HAS_WAVELINK and vc and hasattr(vc, 'filters'):
            try:
                filters = vc.filters
                if state.filter == "nightcore":
                    filters.timescale.set(speed=1.25, pitch=1.25)
                elif state.filter == "vaporwave":
                    filters.timescale.set(speed=0.85, pitch=0.8)
                elif state.filter == "8d":
                    filters.rotation.set(hertz=0.2)
                elif state.filter == "reset":
                    filters.reset()
                await vc.set_filters(filters)
                await interaction.response.send_message(f"⚡ 【{b.node_name}】Lavalink 專業音效濾鏡已**毫秒級即時生效**：**{choice_label}**！")
                return
            except Exception as e:
                print(f"[Filter Error] {e}")

        await interaction.response.send_message(f"🎛️ 【{b.node_name}】音效濾波器已切換至：**{choice_label}**！\n*(極限原生 FFmpeg 引擎將以 384k 錄音室級編碼套用)*")

    @tree.command(name="engine", description="查看目前音訊引擎運作狀態 (Lavalink / 原生 FFmpeg)")
    async def slash_engine(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎧 音訊引擎狀態 (Audio Engine Status)",
            description="DJ Donut 支援 Lavalink v4 與本機原生 FFmpeg 雙模音訊管線：",
            color=0x9B59B6
        )

        lavalink_connected = any(b.lavalink_connected for b in NODE_BOTS)
        lavalink_status = "🟢 已連線 (使用 Lavalink v4 節點)" if lavalink_connected else "⚪ 未連線 (使用本機原生 FFmpeg)"
        node_uri_display = LAVALINK_URI if LAVALINK_URI else "未配置 (可於 .env 設定 LAVALINK_URI)"

        embed.add_field(
            name="⚡ Lavalink v4",
            value=f"• 狀態: `{lavalink_status}`\n• 節點位址: `{node_uri_display}`\n• 支援: 外部音訊節點轉發、即時等化器濾鏡",
            inline=False
        )
        embed.add_field(
            name="🎵 原生 FFmpeg 引擎",
            value="• 狀態: `🟢 就緒`\n• 支援: 最高 384kbps Opus 編碼、預載緩衝區、單執行緒 CPU 保護",
            inline=False
        )
        embed.add_field(name="🤖 機器人數量", value=f"`共 {len(NODE_BOTS)} 隻待命中`", inline=True)
        embed.set_footer(text="提示：可使用 /bassboost 或 /filter 調整音效等化器")
        await interaction.response.send_message(embed=embed)
