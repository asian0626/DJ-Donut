import discord
from discord.ext import commands
from discord import app_commands
from commands.music import register_music_commands
from commands.audio import register_audio_commands
from commands.admin import register_admin_commands

def register_slash_commands(primary_bot: commands.Bot):
    tree = primary_bot.tree
    register_music_commands(tree)
    register_audio_commands(tree)
    register_admin_commands(tree)

    @tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        print(f"[Master Tree Error] {error}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ 執行錯誤: {error}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ 執行錯誤: {error}", ephemeral=True)
        except Exception:
            pass

__all__ = [
    "register_slash_commands",
    "register_music_commands",
    "register_audio_commands",
    "register_admin_commands"
]
