from typing import Optional, Tuple, TYPE_CHECKING
import discord
from core.models import is_vc_connected, is_vc_active
from core.node import NODE_BOTS

if TYPE_CHECKING:
    from core.node import MusicNodeBot

def get_bot_guild(bot: 'MusicNodeBot', guild_id: int, interaction_guild: Optional[discord.Guild] = None) -> Optional[discord.Guild]:
    if interaction_guild and interaction_guild.id == guild_id:
        if hasattr(bot, 'user') and bot.user and hasattr(interaction_guild, 'me') and interaction_guild.me:
            if interaction_guild.me.id == bot.user.id:
                return interaction_guild
        if getattr(bot, 'node_id', None) == 1:
            return interaction_guild

    g = bot.get_guild(guild_id)
    if g:
        return g
    g = discord.utils.get(bot.guilds, id=guild_id)
    if g:
        return g
    return None

async def get_or_fetch_bot_guild(bot: 'MusicNodeBot', guild_id: int, interaction_guild: Optional[discord.Guild] = None) -> Optional[discord.Guild]:
    g = get_bot_guild(bot, guild_id, interaction_guild)
    if g:
        return g
    try:
        fetched = await bot.fetch_guild(guild_id)
        if fetched:
            return fetched
    except Exception:
        pass
    return None

async def find_best_bot_for_channel(
    guild_id: int,
    voice_channel: discord.VoiceChannel,
    interaction_guild: Optional[discord.Guild] = None
) -> Tuple[Optional['MusicNodeBot'], bool]:
    available_bots = []
    for b in NODE_BOTS:
        guild = await get_or_fetch_bot_guild(b, guild_id, interaction_guild)
        if guild:
            available_bots.append((b, guild))

    if not available_bots and interaction_guild and NODE_BOTS:
        available_bots.append((NODE_BOTS[0], interaction_guild))

    if not available_bots:
        return None, False

    def get_vc(bot: 'MusicNodeBot', g: discord.Guild):
        if g and g.voice_client:
            return g.voice_client
        for v in getattr(bot, 'voice_clients', []):
            if v.guild and v.guild.id == guild_id:
                return v
        return None

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        if vc and is_vc_connected(vc) and hasattr(vc, 'channel') and vc.channel and vc.channel.id == voice_channel.id:
            return b, True

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        if not vc or not is_vc_connected(vc):
            return b, False

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        if vc and is_vc_connected(vc) and hasattr(vc, 'channel') and vc.channel:
            real_listeners = [m for m in vc.channel.members if not m.bot]
            if len(real_listeners) == 0:
                return b, False

    for b, guild in available_bots:
        vc = get_vc(b, guild)
        state = b.get_state(guild_id)
        if vc and is_vc_connected(vc):
            if not is_vc_active(vc) and len(state.queue) == 0:
                return b, False

    return None, False

def get_bot_in_user_room(
    guild_id: int,
    user_voice_channel: discord.VoiceChannel,
    interaction_guild: Optional[discord.Guild] = None
) -> Optional['MusicNodeBot']:
    for b in NODE_BOTS:
        guild = get_bot_guild(b, guild_id, interaction_guild)
        if guild:
            vc = guild.voice_client
            if not vc:
                for v in getattr(b, 'voice_clients', []):
                    if v.guild and v.guild.id == guild.id:
                        vc = v
                        break
            if vc and is_vc_connected(vc) and hasattr(vc, 'channel') and vc.channel and vc.channel.id == user_voice_channel.id:
                return b
    return None

async def get_user_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    user_voice = getattr(interaction.user, 'voice', None)
    if user_voice and getattr(user_voice, 'channel', None):
        return user_voice.channel

    guild = interaction.guild
    if not guild:
        return None

    if isinstance(interaction.channel, (discord.VoiceChannel, discord.StageChannel)):
        return interaction.channel

    member = guild.get_member(interaction.user.id)
    if member and getattr(member, 'voice', None) and member.voice.channel:
        return member.voice.channel

    for vc in guild.voice_channels:
        if hasattr(vc, 'voice_states') and interaction.user.id in vc.voice_states:
            return vc
        if hasattr(vc, 'members') and any(m.id == interaction.user.id for m in vc.members):
            return vc

    try:
        fetched_member = await guild.fetch_member(interaction.user.id)
        if fetched_member and getattr(fetched_member, 'voice', None) and fetched_member.voice.channel:
            return fetched_member.voice.channel
    except Exception:
        pass

    return None
