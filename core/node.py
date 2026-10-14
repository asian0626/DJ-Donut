import discord
from discord.ext import commands
import asyncio
import time
import json
import ssl
import urllib.request
from typing import Dict, List, Optional, Any
from config import (
    TOKENS,
    LAVALINK_URI,
    LAVALINK_PASSWORD,
    HAS_WAVELINK,
    logger
)
from core.models import GuildMusicState, is_vc_connected
from core.player import play_next

try:
    import wavelink
except ImportError:
    wavelink = None

async def check_lavalink_health(uri: str, password: str) -> bool:
    if not uri:
        return False
    base_uri = uri.rstrip('/')
    test_url = f"{base_uri}/v4/info"
    loop = asyncio.get_event_loop()

    def _probe_sync():
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            headers = {
                'Authorization': password,
                'User-Agent': 'Wavelink/3.0 (DiscordBot)'
            }
            req = urllib.request.Request(test_url, headers=headers, method='GET')
            with urllib.request.urlopen(req, context=ctx, timeout=2.5) as resp:
                if resp.status == 200:
                    ct = resp.headers.get('Content-Type', '')
                    if 'application/json' in ct:
                        body = resp.read()
                        data = json.loads(body)
                        return bool(data.get('version') or data.get('jvm'))
            return False
        except Exception:
            return False

    return await loop.run_in_executor(None, _probe_sync)

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

        super().__init__(
            command_prefix=[f"{node_id}!", f"!{node_id}", "!"],
            intents=intents,
            help_command=None
        )

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildMusicState(guild_id)
        return self.states[guild_id]

    async def setup_hook(self):
        if HAS_WAVELINK and LAVALINK_URI:
            if len(TOKENS) > 1:
                self.lavalink_connected = False
                if self.node_id == 1:
                    logger.info(f"[{self.node_name}] 🎛️ 檢測到啟用多機矩陣 ({len(TOKENS)} 隻機器人)，為確保各語音房 100% 獨立純淨音訊與發送無衝突，全面啟用極限調校原生獨立引擎！")
            else:
                is_healthy = await check_lavalink_health(LAVALINK_URI, LAVALINK_PASSWORD)
                if is_healthy:
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
                else:
                    self.lavalink_connected = False
                    logger.info(f"[{self.node_name}] ⚠️ Lavalink 節點 ({LAVALINK_URI}) 響應異常或返回非 JSON (404/403)，自動切換至極限原生獨立引擎！")

        if self.node_id == 1:
            logger.info(f"[{self.node_name}] 正在註冊統一調度斜線指令...")
            from commands import register_slash_commands
            register_slash_commands(self)
            await self.tree.sync()
            logger.info(f"[{self.node_name}] 斜線指令同步完畢！")

    async def on_wavelink_node_ready(self, node: 'wavelink.Node'):
        self.lavalink_connected = True
        logger.info(f"[{self.node_name}] 🚀 Lavalink v4 節點 ({node.identifier}) 握手成功，Opus 零損耗直通已啟用！")

    async def on_wavelink_node_closed(self, node: 'wavelink.Node', disconnected: bool):
        self.lavalink_connected = False
        logger.info(f"[{self.node_name}] ⚠️ Lavalink 節點 ({node.identifier}) 斷開連線，已切換為原生引擎。")

    async def on_wavelink_track_end(self, payload: 'wavelink.TrackEndEventPayload'):
        player = getattr(payload, 'player', None)
        if not player or not player.guild:
            return
        if getattr(payload, 'reason', '') in ('REPLACED',):
            return
        await play_next(self, player.guild)

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild
        if member.id == self.user.id and after.channel is None:
            state = self.get_state(guild.id)
            state.loop_mode = 'off'
            state.current = None
            state.queue.clear()
            state.is_playing = False
            return

    async def _periodic_state_cleanup(self):
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
                        if st.current_view:
                            try:
                                st.current_view.stop()
                            except Exception:
                                pass
                        to_remove.append(gid)
            for gid in to_remove:
                self.states.pop(gid, None)

    async def on_ready(self):
        engine_str = "⚡ Lavalink v4 (Opus Passthrough)" if self.lavalink_connected else "🔥 極限原生獨立引擎 (384k VBR)"
        logger.info(f"✅ 【{self.node_name}】已成功連線！名稱: {self.user.name} (音訊引擎: {engine_str})")
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name=f"音樂派對 | {self.node_name} 待命中"
        )
        await self.change_presence(status=discord.Status.online, activity=activity)
        if not hasattr(self, '_cleanup_task_started'):
            self._cleanup_task_started = True
            self.loop.create_task(self._periodic_state_cleanup())
