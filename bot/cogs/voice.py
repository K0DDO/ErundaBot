"""Public voice commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class VoiceCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    voice = app_commands.Group(name="voice", description="Голосовые настройки")

    @voice.command(
        name="toggle",
        description="Вкл/выкл: Ерунда слышит тебя в войсе",
    )
    @app_commands.guild_only()
    async def voice_toggle(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        muted = await self.bot.db.toggle_voice_listen_muted(
            interaction.guild.id,
            interaction.user.id,
        )
        self.bot.voice_listen_service.set_user_muted(
            interaction.guild.id,
            interaction.user.id,
            muted,
        )
        if muted:
            text = "Ерунда тебя не слышит"
        else:
            text = "Ерунда снова слышит"
        await interaction.response.send_message(
            embed=success_embed(text),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(VoiceCog(bot))
