from collections import deque
import random
import time
from typing import TYPE_CHECKING, Any, Optional, Union

import discord

from config import (
    MAX_QUEUE_SIZE,
    add_user_favorite,
    format_duration,
    get_user_favorites,
    logger,
    save_guild_setting,
)
from core.models import (
    GuildMusicState,
    Song,
    get_playback_elapsed,
    has_dj_permission,
    is_vc_active,
    is_vc_connected,
    safe_disconnect,
    safe_pause_resume,
    safe_stop,
    safe_voice_connect,
)
from i18n import resolve_locale, t
from lyrics import format_clean_lyrics, get_lyrics_async
from ui.embeds import build_queue_embed
from ui.player_card import build_player_card

if TYPE_CHECKING:
    from core.node import MusicNodeBot


class QueuePaginationView(discord.ui.View):
    def __init__(self, bot: 'MusicNodeBot', state: GuildMusicState, lang: str = 'zh-TW', per_page: int = 10):
        super().__init__(timeout=180)
        self.bot = bot
        self.state = state
        self.lang = lang
        self.per_page = per_page
        self.page = 1
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _total_pages(self) -> int:
        return max(1, (len(self.state.queue) + self.per_page - 1) // self.per_page)

    def _sync_buttons(self) -> None:
        tp = self._total_pages()
        self.page = max(1, min(self.page, tp))
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id in ("q_first", "q_prev"):
                    child.disabled = (self.page <= 1)
                elif child.custom_id in ("q_next", "q_last"):
                    child.disabled = (self.page >= tp)
                elif child.custom_id == "q_shuffle":
                    child.disabled = (len(self.state.queue) < 2)

    async def _refresh_page(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        embed = build_queue_embed(self.bot, self.state, lang=self.lang, page=self.page, per_page=self.per_page)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        self.stop()
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                logger.debug("Failed to disable QueuePaginationView on timeout", exc_info=True)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="q_first")
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 1
        await self._refresh_page(interaction)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, custom_id="q_prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        await self._refresh_page(interaction)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, custom_id="q_next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self._total_pages(), self.page + 1)
        await self._refresh_page(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="q_last")
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self._total_pages()
        await self._refresh_page(interaction)

    @discord.ui.button(label="隨機打亂", emoji="🔀", style=discord.ButtonStyle.success, custom_id="q_shuffle")
    async def shuffle_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        if len(self.state.queue) >= 2:
            q_list = list(self.state.queue)
            random.shuffle(q_list)
            self.state.queue = deque(q_list)
        await self._refresh_page(interaction)


class _MusicButton(discord.ui.Button['MusicControllerView']):
    def __init__(self, controller: 'MusicControllerView', action: str, emoji: str):
        super().__init__(emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"music_{action}")
        self.action = action
        self.controller = controller

    async def callback(self, interaction: discord.Interaction) -> None:
        await getattr(self.controller, self.action)(interaction)


class _MusicControlSelect(discord.ui.Select['MusicControllerView']):
    def __init__(self, controller: 'MusicControllerView', **kwargs: Any):
        super().__init__(**kwargs)
        self.controller = controller

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.controller.on_control(interaction, self.values[0])


class MusicControllerView(discord.ui.LayoutView):
    def __init__(self, bot: 'MusicNodeBot', state: GuildMusicState, lang: Optional[str] = None):
        super().__init__(timeout=None)
        self.bot = bot
        self.state = state
        self.lang = lang or resolve_locale(guild_id=state.guild_id)
        self._update_button_states()

    def _update_button_states(self) -> None:
        self.clear_items()
        paused = self.state.pause_timestamp > 0
        controls = discord.ui.ActionRow(
            _MusicButton(self, 'shuffle_btn', '🔀'),
            _MusicButton(self, 'replay', '⏮️'),
            _MusicButton(self, 'play_pause', '▶️' if paused else '⏸️'),
            _MusicButton(self, 'skip', '⏭️'),
            _MusicButton(self, 'fav_add', '➕'),
        )
        actions = (
            ('view_queue', 'queue', '📜'),
            ('view_lyrics', 'lyrics', '💬'),
            ('toggle_loop', 'loop', '🔁'),
            ('volume_down', 'volume_down', '🔉'),
            ('volume_up', 'volume_up', '🔊'),
            ('toggle_autoplay', 'autoplay', '📻'),
            ('fav_play', 'favorites', '⭐'),
            ('stop_btn', 'stop', '⏹️'),
        )
        descriptions = {
            'toggle_loop': t(f'loop_{self.state.loop_mode}', lang=self.lang),
            'volume_down': f'{int(self.state.volume * 100)}%',
            'volume_up': f'{int(self.state.volume * 100)}%',
            'toggle_autoplay': '✅' if self.state.autoplay else '❌',
        }
        options = [discord.SelectOption(label=t(f'player_{key}', lang=self.lang), value=action, emoji=emoji,
                                        description=descriptions.get(action)) for action, key, emoji in actions]
        menu = discord.ui.ActionRow(_MusicControlSelect(self,
            custom_id='music_controls', placeholder=t('player_select_control', lang=self.lang),
            min_values=1, max_values=1, options=options,
        ))
        self.add_item(build_player_card(self.bot, self.state, controls, menu, lang=self.lang))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.state.current or (self.state.current_view is not None and self.state.current_view is not self):
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return False
        return True

    def _disable_controls(self) -> None:
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    async def _refresh_interaction(self, interaction: discord.Interaction, *, deferred: bool = False) -> None:
        self._update_button_states()
        if deferred:
            await interaction.edit_original_response(content=None, embeds=[], view=self)
        else:
            await interaction.response.edit_message(content=None, embeds=[], view=self)

    async def on_control(self, interaction: discord.Interaction, action: str) -> None:
        actions = {
            'view_queue': self.view_queue, 'view_lyrics': self.view_lyrics,
            'toggle_loop': self.toggle_loop, 'volume_down': self.volume_down,
            'volume_up': self.volume_up, 'toggle_autoplay': self.toggle_autoplay,
            'fav_play': self.fav_play, 'stop_btn': self.stop_btn,
        }
        handler = actions.get(action)
        if handler is not None:
            await handler(interaction)

    def _get_vc(self, interaction: discord.Interaction) -> Any:
        gid = interaction.guild_id or self.state.guild_id
        bot_guild = self.bot.get_guild(gid) or interaction.guild
        return (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(self.bot, 'voice_clients', []), guild__id=gid)

    async def replay(self, interaction: discord.Interaction):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        vc = self._get_vc(interaction)
        if not vc or not self.state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.last_active = time.time()
        self.state.skip_requested = True
        self.state.queue.appendleft(self.state.current)
        await safe_stop(vc)
        await interaction.response.send_message("⏮️ 重播當前歌曲！", ephemeral=True)

    async def play_pause(self, interaction: discord.Interaction):
        gid = interaction.guild_id or self.state.guild_id
        bot_guild = self.bot.get_guild(gid) or interaction.guild
        vc = self._get_vc(interaction)
        if not vc:
            await interaction.response.send_message(t('no_bot_in_room', interaction), ephemeral=True)
            return
        self.state.last_active = time.time()
        await interaction.response.defer()
        if not is_vc_connected(vc):
            vstate = getattr(interaction.user, 'voice', None)
            user_voice = getattr(vstate, 'channel', None) if vstate else None
            target_ch = user_voice or getattr(vc, 'channel', None) or self.state.voice_channel
            if isinstance(target_ch, (discord.VoiceChannel, discord.StageChannel)):
                vc = await safe_voice_connect(self.bot, target_ch, timeout=30.0)
        await safe_pause_resume(vc, self.state, bot=self.bot, guild=bot_guild)
        try:
            await self._refresh_interaction(interaction, deferred=True)
        except Exception:
            logger.debug("Ignored exception in play_pause", exc_info=True)

    async def skip(self, interaction: discord.Interaction):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        vc = self._get_vc(interaction)
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.last_active = time.time()
        self.state.skip_requested = True
        if self.state.loop_mode == 'single':
            self.state.loop_mode = 'off'
        self.state.cancel_progress_task()
        await safe_stop(vc)
        await interaction.response.send_message(t('skipped', interaction, bot_name=self.bot.node_name))

    async def toggle_loop(self, interaction: discord.Interaction):
        self.state.last_active = time.time()
        modes = ['off', 'single', 'queue']
        idx = modes.index(self.state.loop_mode)
        self.state.loop_mode = modes[(idx + 1) % len(modes)]
        await self._refresh_interaction(interaction)

    async def stop_btn(self, interaction: discord.Interaction):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        vc = self._get_vc(interaction)
        self.state.last_active = time.time()
        self.state.skip_requested = True
        self._disable_controls()
        await interaction.response.edit_message(view=self)
        self.state.cleanup()
        await safe_stop(vc)
        await safe_disconnect(vc)
        self.stop()
        await interaction.followup.send(t('stopped_and_left', interaction, bot_name=self.bot.node_name), ephemeral=True)

    async def volume_down(self, interaction: discord.Interaction):
        vc = self._get_vc(interaction)
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.last_active = time.time()
        self.state.volume = max(0.0, round(self.state.volume - 0.1, 2))
        save_guild_setting(self.state.guild_id, "volume", self.state.volume)
        if hasattr(vc, 'source') and hasattr(vc.source, 'volume'):
            vc.source.volume = self.state.volume
        await self._refresh_interaction(interaction)

    async def volume_up(self, interaction: discord.Interaction):
        vc = self._get_vc(interaction)
        if not vc:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        self.state.last_active = time.time()
        self.state.volume = min(2.0, round(self.state.volume + 0.1, 2))
        save_guild_setting(self.state.guild_id, "volume", self.state.volume)
        if hasattr(vc, 'source') and hasattr(vc.source, 'volume'):
            vc.source.volume = self.state.volume
        await self._refresh_interaction(interaction)

    async def toggle_autoplay(self, interaction: discord.Interaction):
        self.state.autoplay = not self.state.autoplay
        save_guild_setting(self.state.guild_id, "autoplay", self.state.autoplay)
        await self._refresh_interaction(interaction)

    async def view_lyrics(self, interaction: discord.Interaction):
        if not self.state.current:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        target_title = self.state.current.title
        target_artist = self.state.current.uploader or ""
        res = self.state.current_lyrics or await get_lyrics_async(target_title, target_artist)
        if not res:
            await interaction.followup.send(t('lyrics_not_found', interaction, title=target_title), ephemeral=True)
            return
        self.state.current_lyrics = res
        display_title, lrc_text, is_synced = res
        elapsed = get_playback_elapsed(self.state)
        clean_body = format_clean_lyrics(lrc_text, is_synced, elapsed_sec=elapsed)
        if len(clean_body) > 4000:
            clean_body = clean_body[:3990] + "\n..."
        embed = discord.Embed(
            title=f"💬 【{display_title}】即時動態歌詞",
            description=clean_body,
            color=0x1DB954 if is_synced else 0x5865F2
        )
        sync_str = "⚡ 動態時間軸高亮 (Synced Karaoke)" if is_synced else "📝 純文字 (Plain)"
        embed.set_footer(text=f"模式: {sync_str} • 資料庫: LRCLIB / Netease")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def view_queue(self, interaction: discord.Interaction):
        lang = resolve_locale(interaction)
        embed = build_queue_embed(self.bot, self.state, lang=lang, page=1)
        view = QueuePaginationView(self.bot, self.state, lang=lang)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            view.message = await interaction.original_response()
        except Exception:
            logger.debug("Failed to get original_response for QueuePaginationView", exc_info=True)

    async def fav_add(self, interaction: discord.Interaction):
        cur = self.state.current
        if not cur:
            await interaction.response.send_message(t('not_playing', interaction), ephemeral=True)
            return
        song_item = {
            "title": cur.title,
            "url": cur.url or f"ytsearch5:{cur.title}",
            "duration": cur.duration or 0,
            "thumbnail": cur.thumbnail or "",
            "uploader": cur.uploader or ""
        }
        added, total_count = add_user_favorite(interaction.user.id, song_item)
        if added:
            await interaction.response.send_message(
                f"❤️ 已將 **{cur.title}** 加入您的個人最愛收藏！（目前共 `{total_count}` 首，隨時可按 `選擇控制 → 播放收藏` 或 `/favorite play` 點播）",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"💡 **{cur.title}** 已經在您的個人最愛收藏清單中囉！（共 `{total_count}` 首）",
                ephemeral=True
            )

    async def fav_play(self, interaction: discord.Interaction):
        favs = get_user_favorites(interaction.user.id)
        if not favs:
            await interaction.response.send_message("⭐ 您的個人收藏清單目前是空的！先按`➕` 收藏按鈕將喜歡的歌曲存起來吧！", ephemeral=True)
            return
        if self.state.queue_space() <= 0:
            await interaction.response.send_message(t('queue_full', interaction, max_size=MAX_QUEUE_SIZE), ephemeral=True)
            return
        added_count = 0
        for item in favs[:self.state.queue_space()]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            s = Song({
                "title": item.get("title"),
                "webpage_url": item.get("url"),
                "url": item.get("url") or f"ytsearch5:{item.get('title')}",
                "duration": item.get("duration", 0),
                "thumbnail": item.get("thumbnail", ""),
                "uploader": item.get("uploader", "個人收藏")
            }, interaction.user)
            self.state.queue.append(s)
            added_count += 1
        self.state.last_active = time.time()
        await self._refresh_interaction(interaction)
        await interaction.followup.send(f"⭐ 已成功將您收藏的 **`{added_count}`** 首最愛歌曲排入當前待播隊列！", ephemeral=True)

    async def shuffle_btn(self, interaction: discord.Interaction):
        if not has_dj_permission(interaction, self.state):
            await interaction.response.send_message(t('dj_permission_denied', interaction, role_id=self.state.dj_role_id), ephemeral=True)
            return
        if len(self.state.queue) < 2:
            await interaction.response.send_message(t('queue_empty', interaction), ephemeral=True)
            return
        q_list = list(self.state.queue)
        random.shuffle(q_list)
        self.state.queue = deque(q_list)
        self.state.last_active = time.time()
        await self._refresh_interaction(interaction)
        await interaction.followup.send(t('shuffle_success', interaction, count=len(self.state.queue)), ephemeral=True)


async def refresh_nowplaying_message(bot: 'MusicNodeBot', state: GuildMusicState) -> None:
    message = state.nowplaying_message
    if message is None:
        return
    view = state.current_view
    if not isinstance(view, MusicControllerView) or view.is_finished():
        if view is not None:
            view.stop()
        view = MusicControllerView(bot, state)
        state.current_view = view
    else:
        view._update_button_states()
    await message.edit(content=None, embeds=[], view=view)


class _SearchSelectMenu(discord.ui.Select['SearchSelectView']):
    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            await self.view.on_select(interaction)


class SearchSelectView(discord.ui.View):
    def __init__(self, bot: 'MusicNodeBot', guild: Optional[discord.Guild], user: Union[discord.Member, discord.User], results: list):
        super().__init__(timeout=60)
        self.bot = bot
        self.guild = guild
        self.user = user
        self.results = results
        self.message: Optional[discord.Message] = None

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

        self.select_menu: _SearchSelectMenu = _SearchSelectMenu(
            placeholder="請點選此處挑選欲播放的歌曲...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("❌ 只有發起搜尋的人才能選擇歌曲！", ephemeral=True)
            return

        guild = self.guild or interaction.guild
        if not guild:
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

        state = self.bot.get_state(guild.id)
        if state.queue_space() <= 0:
            await interaction.response.send_message(t('queue_full', interaction, max_size=MAX_QUEUE_SIZE), ephemeral=True)
            return
        if interaction.channel and hasattr(interaction.channel, 'send'):
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
        bot_guild = get_bot_guild(self.bot, guild.id, interaction.guild) or guild
        vc = (bot_guild.voice_client if bot_guild else None) or discord.utils.get(getattr(self.bot, 'voice_clients', []), guild__id=guild.id)
        if not is_vc_active(vc) and bot_guild:
            await play_next(self.bot, bot_guild)

    async def on_timeout(self) -> None:
        self.select_menu.disabled = True
        self.stop()
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                logger.debug("Failed to disable SearchSelectView on timeout", exc_info=True)
