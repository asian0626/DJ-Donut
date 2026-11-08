import asyncio
import time
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord.ext import commands

from config import (
    HAS_WAVELINK,
    LAVALINK_INSECURE_TLS,
    LAVALINK_PASSWORD,
    LAVALINK_URI,
    QUEUE_SNAPSHOT_INTERVAL,
    RESTORE_QUEUES,
    TOKENS,
    logger,
    wavelink,
)
from core.models import (
    GuildMusicState,
    Song,
    get_playback_elapsed,
    is_vc_connected,
    safe_disconnect,
    safe_stop,
    safe_voice_connect,
)
from core.player import play_next
from httpclient import get_http_session
from storage import delete_queue_snapshot, load_queue_snapshots, save_queue_snapshot


async def check_lavalink_health(uri: str, password: str) -> bool:
    """探測 Lavalink /v4/info。預設驗證 TLS 憑證，僅在 LAVALINK_INSECURE_TLS=1 時略過。"""
    if not uri:
        return False
    test_url = f"{uri.rstrip('/')}/v4/info"
    headers = {
        'Authorization': password,
        'User-Agent': 'Wavelink/3.0 (DiscordBot)'
    }
    try:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=2.5)
        ssl_opt: Any = False if LAVALINK_INSECURE_TLS else None
        async with session.get(test_url, headers=headers, timeout=timeout, ssl=ssl_opt) as resp:
            if resp.status != 200 or 'application/json' not in resp.headers.get('Content-Type', ''):
                return False
            data = await resp.json(content_type=None)
            return bool(data.get('version') or data.get('jvm'))
    except Exception as e:
        logger.debug(f"Lavalink health probe failed: {e}")
        return False


NODE_BOTS: List['MusicNodeBot'] = []


class MusicNodeBot(commands.Bot):
    def __init__(self, node_id: int):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True

        self.node_id = node_id
        self.node_name = f"音樂 {node_id} 號"
        self.states: Dict[int, GuildMusicState] = {}
        self.lavalink_connected = False
        self._cleanup_task_started = False

        super().__init__(
            command_prefix=[f"{node_id}!", f"!{node_id}", "!"],
            intents=intents,
            help_command=None
        )

    def get_state(self, guild_id: Optional[int]) -> GuildMusicState:
        gid = int(guild_id or 0)
        if gid not in self.states:
            self.states[gid] = GuildMusicState(gid)
        return self.states[gid]

    async def _init_lavalink_if_configured(self) -> None:
        if not (HAS_WAVELINK and LAVALINK_URI):
            return
        if len(TOKENS) > 1:
            self.lavalink_connected = False
            if self.node_id == 1:
                logger.info(f"[{self.node_name}] 🎛️ 檢測到啟用多機矩陣 ({len(TOKENS)} 隻機器人)，為確保各語音房 100% 獨立純淨音訊與發送無衝突，全面啟用極限調校原生獨立引擎！")
            return
        if not await check_lavalink_health(LAVALINK_URI, LAVALINK_PASSWORD):
            self.lavalink_connected = False
            logger.info(f"[{self.node_name}] ⚠️ Lavalink 節點 ({LAVALINK_URI}) 響應異常或返回非 JSON (404/403)，自動切換至極限原生獨立引擎！")
            return
        try:
            node = wavelink.Node(
                identifier=f"Node_{self.node_id}",
                uri=LAVALINK_URI,
                password=LAVALINK_PASSWORD
            )
            await wavelink.Pool.connect(nodes=[node], client=self)
            self.lavalink_connected = True
            logger.info(f"[{self.node_name}] 🚀 成功連線至商業級 Lavalink v4 音訊節點 ({LAVALINK_URI})！")
        except Exception as e:
            self.lavalink_connected = False
            logger.info(f"[{self.node_name}] ⚠️ Lavalink 連線失敗，自動無縫啟用極限調校原生引擎: {e}")

    async def setup_hook(self) -> None:
        await self._init_lavalink_if_configured()
        if self.node_id == 1:
            logger.info(f"[{self.node_name}] 正在註冊統一調度斜線指令...")
            from commands import register_slash_commands
            register_slash_commands(self)
            await self.tree.sync()
            logger.info(f"[{self.node_name}] 斜線指令同步完畢！")

    async def on_wavelink_node_ready(self, node: Any) -> None:
        self.lavalink_connected = True
        logger.info(f"[{self.node_name}] 🚀 Lavalink v4 節點 ({node.identifier}) 握手成功，Opus 零損耗直通已啟用！")

    async def on_wavelink_node_closed(self, node: Any, disconnected: bool) -> None:
        self.lavalink_connected = False
        logger.info(f"[{self.node_name}] ⚠️ Lavalink 節點 ({node.identifier}) 斷開連線，已切換為原生引擎。")

    async def on_wavelink_track_end(self, payload: Any) -> None:
        player = getattr(payload, 'player', None)
        if not player or not player.guild:
            return
        if getattr(payload, 'reason', '') in ('REPLACED',):
            return
        await play_next(self, player.guild)

    async def _handle_self_disconnected(self, guild: discord.Guild, prev_channel: Any) -> None:
        state = self.get_state(guild.id)
        was_unexpected = (state.voice_channel is not None or state.is_playing) and not state.skip_requested
        old_np_msg = state.nowplaying_message
        text_ch = state.text_channel
        state.cleanup()
        if old_np_msg:
            try:
                await old_np_msg.delete()
            except Exception:
                logger.debug("Ignored exception in _handle_self_disconnected", exc_info=True)
        bg = self.get_guild(guild.id) or guild
        vc = (bg.voice_client if bg else None) or discord.utils.get(getattr(self, 'voice_clients', []), guild__id=guild.id)
        if vc:
            await safe_stop(vc)
            await safe_disconnect(vc)
        if was_unexpected and text_ch and prev_channel:
            try:
                from i18n import t
                embed = discord.Embed(
                    title=t('bot_kicked_title', guild_id=guild.id),
                    description=t('bot_kicked_desc', guild_id=guild.id, bot_name=self.node_name, channel=prev_channel.name),
                    color=0xFFB6C1
                )
                embed.set_footer(text=t('bot_kicked_footer', guild_id=guild.id))
                if self.user and self.user.display_avatar:
                    embed.set_thumbnail(url=self.user.display_avatar.url)
                await text_ch.send(embed=embed)
            except Exception:
                logger.debug("Ignored exception in _handle_self_disconnected", exc_info=True)

    def _schedule_auto_leave(self, vc: Any, state: GuildMusicState) -> None:
        t_task = state._auto_leave_task
        if t_task and not t_task.done():
            return

        async def _auto_leave_coro() -> None:
            try:
                await asyncio.sleep(60)
                ch = getattr(vc, 'channel', None) if vc else None
                members = getattr(ch, 'members', None) if ch else None
                if vc and is_vc_connected(vc) and members is not None:
                    cur_humans = [m for m in members if not m.bot]
                    if len(cur_humans) == 0 and not state.mode_247:
                        state.cleanup()
                        await safe_stop(vc)
                        await safe_disconnect(vc)
            except asyncio.CancelledError:
                pass

        state._auto_leave_task = self.loop.create_task(_auto_leave_coro())

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        guild = member.guild
        bot_uid = self.user.id if self.user else None
        if member.id == bot_uid:
            if after.channel is not None:
                self.get_state(guild.id).voice_channel = after.channel
            else:
                await self._handle_self_disconnected(guild, before.channel)
            return

        bg = self.get_guild(guild.id) or guild
        vc = (bg.voice_client if bg else None) or discord.utils.get(getattr(self, 'voice_clients', []), guild__id=guild.id)
        vc_ch = getattr(vc, 'channel', None) if vc else None
        if not vc or not is_vc_connected(vc) or not vc_ch:
            return

        state = self.get_state(guild.id)
        if after.channel == vc_ch and not member.bot:
            t_task = state._auto_leave_task
            if t_task and not t_task.done():
                t_task.cancel()
                state._auto_leave_task = None

        if before.channel == vc_ch and after.channel != vc_ch:
            members = getattr(vc_ch, 'members', [])
            humans = [m for m in members if not m.bot]
            if len(humans) == 0 and not state.mode_247:
                self._schedule_auto_leave(vc, state)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        st = self.states.pop(guild.id, None)
        if st:
            st.cleanup()

    async def _periodic_state_cleanup(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(600)
            now = time.time()
            to_remove = []
            for gid, st in list(self.states.items()):
                if not st.is_playing and len(st.queue) == 0 and (now - st.last_active > 1800):
                    bg = self.get_guild(gid)
                    vc = bg.voice_client if bg else None
                    if not vc or not is_vc_connected(vc):
                        st.cleanup()
                        to_remove.append(gid)
            for gid in to_remove:
                self.states.pop(gid, None)

    def _snapshot_single_state(self, gid: int, st: GuildMusicState) -> None:
        try:
            if (st.current or st.queue) and st.voice_channel:
                save_queue_snapshot(gid, self.node_id, {
                    'voice_channel_id': getattr(st.voice_channel, 'id', None),
                    'text_channel_id': getattr(st.text_channel, 'id', None),
                    'elapsed': get_playback_elapsed(st),
                    'current': st.current.to_snapshot() if st.current else None,
                    'queue': [song.to_snapshot() for song in list(st.queue)[:200]],
                })
            else:
                delete_queue_snapshot(gid, self.node_id)
        except Exception:
            logger.debug("Ignored exception in _snapshot_single_state", exc_info=True)

    def snapshot_all_states(self) -> None:
        """將此節點所有活躍語音房的播放隊列寫入 SQLite，供重啟後恢復。"""
        for gid, st in list(self.states.items()):
            self._snapshot_single_state(gid, st)

    async def _periodic_queue_snapshot(self) -> None:
        await self.wait_until_ready()
        if QUEUE_SNAPSHOT_INTERVAL <= 0:
            return
        while not self.is_closed():
            await asyncio.sleep(QUEUE_SNAPSHOT_INTERVAL)
            self.snapshot_all_states()

    async def _restore_guild_snapshot(self, gid: int, snap: dict) -> None:
        try:
            guild = self.get_guild(gid)
            vch_id = snap.get('voice_channel_id')
            vch = guild.get_channel(vch_id) if (guild and vch_id) else None
            humans = [m for m in getattr(vch, 'members', []) if not getattr(m, 'bot', False)] if vch else []
            items = ([snap['current']] if snap.get('current') else []) + (snap.get('queue') or [])
            if not guild or not vch or not humans or not items:
                delete_queue_snapshot(gid, self.node_id)
                return
            st = self.get_state(gid)
            if st.is_playing or st.queue:
                return
            tch_id = snap.get('text_channel_id')
            if tch_id:
                st.text_channel = guild.get_channel(tch_id)
            st.voice_channel = vch
            for it in items[:200]:
                req_id = it.get('requester_id')
                requester = (guild.get_member(req_id) if req_id else None) or guild.me or humans[0]
                st.queue.append(Song.from_snapshot(it, requester))
            await safe_voice_connect(self, vch, timeout=20.0)
            logger.info(f"[{self.node_name}] 🔄 已為伺服器 {guild.name} 恢復 {len(st.queue)} 首中斷前的待播歌曲")
            await play_next(self, guild)
        except Exception as e:
            logger.warning(f"[{self.node_name}] 恢復隊列快照失敗 (guild={gid}): {e}")

    async def _restore_persisted_queues(self) -> None:
        if not RESTORE_QUEUES:
            return
        for gid, snap in load_queue_snapshots(self.node_id).items():
            await self._restore_guild_snapshot(gid, snap)

    async def on_ready(self) -> None:
        engine_str = "⚡ Lavalink v4 (Opus Passthrough)" if self.lavalink_connected else "🔥 極限原生獨立引擎 (384k VBR)"
        bot_name_str = self.user.name if self.user else self.node_name
        logger.info(f"✅ 【{self.node_name}】已成功連線！名稱: {bot_name_str} (音訊引擎: {engine_str})")
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name=f"音樂派對 | {self.node_name} 待命中"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)
        if not self._cleanup_task_started:
            self._cleanup_task_started = True
            self.loop.create_task(self._periodic_state_cleanup())
            self.loop.create_task(self._periodic_queue_snapshot())
            self.loop.create_task(self._restore_persisted_queues())
