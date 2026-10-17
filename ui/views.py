import discord
import asyncio
import time
from typing import Optional, List, Any
from config import save_guild_setting, MEDIA_EXECUTOR, format_duration, logger
from core.models import (
    Song,
    GuildMusicState,
    safe_pause_resume,
    safe_stop,
    safe_disconnect,
    has_dj_permission,
    is_vc_active
)
from ui.embeds import build_nowplaying_embed, build_queue_embed
from i18n import t, resolve_locale
from lyrics import get_lyrics

class MusicControllerView(discord.ui.View):
    def __init__(self, bot: 'MusicNodeBot', state: GuildMusicState):
        super().__init__(timeout=None)
        self.bot = bot
        self.state = state
        self._update_button_states()

    def _update_button_states(self):
        is_paused = (self.state.pause_timestamp > 0)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "music_play_pause":
                    if is_paused:
                        child.emoji = "▶️"
                        child.style = discord.ButtonStyle.secondary
                    else:
                        child.emoji = "⏸️"
                        child.style = discord.ButtonStyle.primary
                elif child.custom_id == "music_loop":
                    if self.state.loop_mode == 'single':
                        child.emoji = "🔂"
                        child.label = "單曲"
                        child.style = discord.ButtonStyle.success
                    elif self.state.loop_mode == 'queue':
                        child.emoji = "🔁"
                        child.label = "歌單"
                        child.style = discord.ButtonStyle.primary
                    else:
                        child.emoji = "🔁"
                        child.label = "循環"
                        child.style = discord.ButtonStyle.secondary
                elif child.custom_id == "music_autoplay":
                    child.style = discord.ButtonStyle.success if self.state.autoplay else discord.ButtonStyle.secondary

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0, custom_id="music_replay")
    async def replay(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or not self.state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.queue.appendleft(self.state.current)
        await safe_stop(vc)
        await interaction.response.send_message("⏮️ 重播當前歌曲！", ephemeral=True)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary, row=0, custom_id="music_play_pause")
    async def play_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return
        await interaction.response.defer()
        res = await safe_pause_resume(vc, self.state, bot=self.bot, guild=interaction.guild)
        self._update_button_states()
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            pass

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0, custom_id="music_skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        if self.state.loop_mode == 'single':
            self.state.loop_mode = 'off'
        self.state.cancel_progress_task()
        await safe_stop(vc)
        await interaction.response.send_message(t('skipped', interaction, bot_name=self.bot.node_name))

    @discord.ui.button(emoji="🔁", label="循環", style=discord.ButtonStyle.secondary, row=0, custom_id="music_loop")
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        modes = ['off', 'single', 'queue']
        idx = modes.index(self.state.loop_mode)
        self.state.loop_mode = modes[(idx + 1) % len(modes)]
        self._update_button_states()
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0, custom_id="music_stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        vc = interaction.guild.voice_client
        self.state.cleanup()
        await safe_stop(vc)
        await safe_disconnect(vc)
        self.stop()
        await interaction.response.send_message(t('stopped_and_left', interaction, bot_name=self.bot.node_name))

    @discord.ui.button(label="-10%", emoji="🔉", style=discord.ButtonStyle.secondary, row=1, custom_id="music_vol_down")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.volume = max(0.0, round(self.state.volume - 0.1, 2))
        save_guild_setting(self.state.guild_id, "volume", self.state.volume)
        if hasattr(vc, 'source') and hasattr(vc.source, 'volume'):
            vc.source.volume = self.state.volume
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="+10%", emoji="🔊", style=discord.ButtonStyle.secondary, row=1, custom_id="music_vol_up")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.volume = min(2.0, round(self.state.volume + 0.1, 2))
        save_guild_setting(self.state.guild_id, "volume", self.state.volume)
        if hasattr(vc, 'source') and hasattr(vc.source, 'volume'):
            vc.source.volume = self.state.volume
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="接歌", emoji="📻", style=discord.ButtonStyle.secondary, row=1, custom_id="music_autoplay")
    async def toggle_autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.state.autoplay = not self.state.autoplay
        save_guild_setting(self.state.guild_id, "autoplay", self.state.autoplay)
        self._update_button_states()
        embed = build_nowplaying_embed(self.bot, self.state.current, self.state, lang=resolve_locale(interaction))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="歌詞", emoji="💬", style=discord.ButtonStyle.secondary, row=1, custom_id="music_lyrics")
    async def view_lyrics(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        target_title = self.state.current.title
        target_artist = self.state.current.uploader or ""
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(MEDIA_EXECUTOR, lambda: get_lyrics(target_title, target_artist))
        if not res:
            await interaction.followup.send(t('lyrics_not_found', interaction, title=target_title), ephemeral=True)
            return
        display_title, lrc_text, is_synced = res
        if len(lrc_text) > 4000:
            lrc_text = lrc_text[:3990] + "\n..."
        embed = discord.Embed(
            title=f"💬 【{display_title}】即時歌詞",
            description=lrc_text,
            color=0x1DB954 if is_synced else 0x5865F2
        )
        sync_str = "⚡ 動態時間軸 (Synced)" if is_synced else "📝 純文字 (Plain)"
        embed.set_footer(text=f"模式: {sync_str} • 資料庫: LRCLIB / Netease")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="隊列", emoji="📜", style=discord.ButtonStyle.secondary, row=1, custom_id="music_queue")
    async def view_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_queue_embed(self.bot, self.state, lang=resolve_locale(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

class SearchSelectView(discord.ui.View):
    def __init__(self, bot: 'MusicNodeBot', guild: discord.Guild, user: discord.Member, results: list):
        super().__init__(timeout=60)
        self.bot = bot
        self.guild = guild
        self.user = user
        self.results = results

        options = []
        for i, item in enumerate(results[:5]):
            title = item.get('title', '未知歌曲')[:90]
            dur = format_duration(item.get('duration', 0))
            uploader = item.get('uploader', '未知上傳者')[:30]
            options.append(discord.SelectOption(
                label=f"{i+1}. {title}",
                value=str(i),
                description=f"時長: {dur} | 上傳者: {uploader}"[:100]
            ))

        self.select_menu = discord.ui.Select(
            placeholder="請點選此處挑選欲播放的歌曲...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ 只有發起搜尋的人才能選擇歌曲！", ephemeral=True)
            return

        idx = int(self.select_menu.values[0])
        item = self.results[idx]
        title = item.get('title', '未知歌曲')
        vid = item.get('id')
        song_url = item.get('webpage_url')
        if not song_url and vid:
            song_url = f"https://www.youtube.com/watch?v={vid}"
        elif not song_url:
            raw_url = item.get('url', '')
            song_url = raw_url if raw_url.startswith('http') else (f"https://www.youtube.com/watch?v={vid}" if vid else title)

        song = Song({
            'id': vid,
            'title': title,
            'webpage_url': song_url,
            'url': song_url,
            'duration': item.get('duration', 0),
            'thumbnail': item.get('thumbnail', ''),
            'uploader': item.get('uploader', '未知上傳者')
        }, interaction.user)

        state = self.bot.get_state(self.guild.id)
        state.text_channel = interaction.channel
        state.queue.append(song)

        self.select_menu.disabled = True
        embed = discord.Embed(
            title=f"✅ 【{self.bot.node_name}】已成功選擇並加入待播隊列！",
            description=f"**[{song.title}]({song.url})**",
            color=0x2ECC71
        )
        embed.add_field(name="⏱️ 時長", value=f"`{format_duration(song.duration)}`", inline=True)
        embed.add_field(name="📍 隊列位置", value=f"`第 {len(state.queue)} 位`", inline=True)
        embed.add_field(name="👤 點播者", value=interaction.user.mention, inline=True)
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)

        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

        from core.dispatcher import get_bot_guild
        from core.player import play_next
        bot_guild = get_bot_guild(self.bot, self.guild.id, interaction.guild) or self.guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(self.bot, 'voice_clients', []), guild__id=self.guild.id)
        if not is_vc_active(vc):
            await play_next(self.bot, bot_guild)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        self.stop()

