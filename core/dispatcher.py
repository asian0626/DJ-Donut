from typing import TYPE_CHECKING, Any, List, Optional, Tuple

import discord

from config import logger
from core.models import is_vc_active, is_vc_connected
from core.node import NODE_BOTS

if TYPE_CHECKING:
    from core.node import MusicNodeBot


def get_bot_guild(bot: 'MusicNodeBot', guild_id: Optional[int], interaction_guild: Optional[discord.Guild] = None) -> Optional[discord.Guild]:
    if not guild_id:
        return interaction_guild
    if interaction_guild and interaction_guild.id == guild_id:
        if bot.user and interaction_guild.me and interaction_guild.me.id == bot.user.id:
            return interaction_guild
        if getattr(bot, 'node_id', None) == 1:
            return interaction_guild

    return bot.get_guild(guild_id) or discord.utils.get(bot.guilds, id=guild_id)


async def get_or_fetch_bot_guild(bot: 'MusicNodeBot', guild_id: Optional[int], interaction_guild: Optional[discord.Guild] = None) -> Optional[discord.Guild]:
    if not guild_id:
        return interaction_guild
    g = get_bot_guild(bot, guild_id, interaction_guild)
    if g:
        return g
    try:
        return await bot.fetch_guild(guild_id)
    except Exception:
        logger.debug("Ignored exception in get_or_fetch_bot_guild", exc_info=True)
    return None


def _get_guild_vc(bot: 'MusicNodeBot', guild: discord.Guild, guild_id: int) -> Any:
    if guild and guild.voice_client:
        return guild.voice_client
    for v in getattr(bot, 'voice_clients', []):
        if v.guild and v.guild.id == guild_id:
            return v
    return None


def _select_bot_from_candidates(
    available_bots: List[Tuple['MusicNodeBot', discord.Guild]],
    guild_id: int,
    channel_id: int,
) -> Tuple[Optional['MusicNodeBot'], bool]:
    for b, guild in available_bots:
        vc = _get_guild_vc(b, guild, guild_id)
        ch = getattr(vc, 'channel', None) if vc else None
        if vc and is_vc_connected(vc) and getattr(ch, 'id', None) == channel_id:
            return b, True

    for b, guild in available_bots:
        vc = _get_guild_vc(b, guild, guild_id)
        if not vc or not is_vc_connected(vc):
            return b, False

    for b, guild in available_bots:
        vc = _get_guild_vc(b, guild, guild_id)
        ch = getattr(vc, 'channel', None) if vc else None
        members = getattr(ch, 'members', None) if ch else None
        if vc and is_vc_connected(vc) and members is not None and not any(not m.bot for m in members):
            return b, False

    for b, guild in available_bots:
        vc = _get_guild_vc(b, guild, guild_id)
        state = b.get_state(guild_id)
        if vc and is_vc_connected(vc) and not is_vc_active(vc) and len(state.queue) == 0:
            return b, False

    return None, False


async def find_best_bot_for_channel(
    guild_id: Optional[int],
    voice_channel: Any,
    interaction_guild: Optional[discord.Guild] = None
) -> Tuple[Optional['MusicNodeBot'], bool]:
    gid = guild_id or (interaction_guild.id if interaction_guild else 0)
    if not gid or not voice_channel:
        return None, False

    available_bots: List[Tuple['MusicNodeBot', discord.Guild]] = []
    for b in NODE_BOTS:
        guild = await get_or_fetch_bot_guild(b, gid, interaction_guild)
        if guild:
            available_bots.append((b, guild))

    if not available_bots and interaction_guild and NODE_BOTS:
        available_bots.append((NODE_BOTS[0], interaction_guild))

    if not available_bots:
        return None, False

    return _select_bot_from_candidates(available_bots, gid, getattr(voice_channel, 'id', 0))


def get_bot_in_user_room(
    guild_id: Optional[int],
    user_voice_channel: Any,
    interaction_guild: Optional[discord.Guild] = None
) -> Optional['MusicNodeBot']:
    gid = guild_id or (interaction_guild.id if interaction_guild else 0)
    if not gid or not user_voice_channel:
        return None
    target_id = getattr(user_voice_channel, 'id', None)
    for b in NODE_BOTS:
        guild = get_bot_guild(b, gid, interaction_guild)
        if not guild:
            continue
        vc = _get_guild_vc(b, guild, guild.id)
        ch = getattr(vc, 'channel', None) if vc else None
        if vc and is_vc_connected(vc) and getattr(ch, 'id', None) == target_id:
            return b
    return None


async def get_user_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    user_voice = getattr(interaction.user, 'voice', None)
    ch = getattr(user_voice, 'channel', None) if user_voice else None
    if isinstance(ch, discord.VoiceChannel):
        return ch

    guild = interaction.guild
    if not guild:
        return None

    if isinstance(interaction.channel, discord.VoiceChannel):
        return interaction.channel

    member = guild.get_member(interaction.user.id)
    m_voice = getattr(member, 'voice', None) if member else None
    m_ch = getattr(m_voice, 'channel', None) if m_voice else None
    if isinstance(m_ch, discord.VoiceChannel):
        return m_ch

    for vc in guild.voice_channels:
        if interaction.user.id in getattr(vc, 'voice_states', {}) or any(m.id == interaction.user.id for m in vc.members):
            return vc

    try:
        fetched_member = await guild.fetch_member(interaction.user.id)
        f_voice = getattr(fetched_member, 'voice', None) if fetched_member else None
        f_ch = getattr(f_voice, 'channel', None) if f_voice else None
        if isinstance(f_ch, discord.VoiceChannel):
            return f_ch
    except Exception:
        logger.debug("Ignored exception in get_user_voice_channel", exc_info=True)

    return None
