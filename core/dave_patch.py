import contextlib
import logging

import discord.gateway
import discord.voice_state

logger = logging.getLogger("DJDonut")

_PATCHED = False


def apply_dave_fix() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    # 1. 修復 can_encrypt 判定並在需要時自動完成初始 DAVE 過渡 (transition 0)
    def patched_can_encrypt(self: discord.voice_state.VoiceConnectionState) -> bool:
        if self.dave_protocol_version != 0 and self.dave_session is not None:
            if not self.dave_session.ready:
                with contextlib.suppress(Exception):
                    self.dave_session.prepare_transition(0, self.dave_protocol_version or 1)
                    self.dave_session.execute_transition(0)
            return bool(self.dave_session.ready)
        return False

    discord.voice_state.VoiceConnectionState.can_encrypt = property(patched_can_encrypt)

    # 2. 修復 Voice Gateway 二進位封包處理：收到 MLS 提交後自動就緒
    orig_received_binary = discord.gateway.DiscordVoiceWebSocket.received_binary_message

    async def patched_received_binary_message(self: discord.gateway.DiscordVoiceWebSocket, msg: bytes) -> None:
        await orig_received_binary(self, msg)
        state = self._connection
        if state and state.dave_session and not state.dave_session.ready:
            with contextlib.suppress(Exception):
                state.dave_session.prepare_transition(0, state.dave_protocol_version or 1)
                state.dave_session.execute_transition(0)

    discord.gateway.DiscordVoiceWebSocket.received_binary_message = patched_received_binary_message

    # 3. 修復 _voice_connect 在連線斷線重試時 channel 可能為 None 的問題
    orig_voice_connect = discord.voice_state.VoiceConnectionState._voice_connect

    async def patched_voice_connect(
        self: discord.voice_state.VoiceConnectionState,
        *,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        channel = getattr(self.voice_client, "channel", None)
        if channel and hasattr(channel, "guild"):
            await channel.guild.change_voice_state(channel=channel, self_deaf=self_deaf, self_mute=self_mute)
        else:
            with contextlib.suppress(Exception):
                await orig_voice_connect(self, self_deaf=self_deaf, self_mute=self_mute)

    discord.voice_state.VoiceConnectionState._voice_connect = patched_voice_connect

    logger.info("🛡️ DAVE E2EE 語音安全加密引擎已啟用並修復自動就緒協商")
