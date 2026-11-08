import discord
from discord.ext import commands
from discord import app_commands
from commands.music import register_music_commands
from commands.audio import register_audio_commands
from commands.admin import register_admin_commands
from config import logger
from i18n import t

def register_slash_commands(primary_bot: commands.Bot):
    tree = primary_bot.tree
    register_music_commands(tree)
    register_audio_commands(tree)
    register_admin_commands(tree)

    @tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = t('command_cooldown', interaction, retry_after=error.retry_after)
        else:
            logger.error(f"[CommandTree Error] {error}", exc_info=error)
            msg = f"❌ 執行錯誤: {error}"
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            logger.debug("Ignored exception in on_app_command_error", exc_info=True)

__all__ = [
    "register_admin_commands",
    "register_audio_commands",
    "register_music_commands",
    "register_slash_commands",
]
