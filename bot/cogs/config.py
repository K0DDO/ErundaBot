"""Server configuration commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import error_embed
from bot.utils.permissions import can_edit_config, config_denied_reason
from bot.views.config_views import ConfigPanel, config_overview_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class ConfigCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    @app_commands.command(name="config", description="Настройки Ерунды для этого сервера")
    @app_commands.guild_only()
    async def config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=error_embed("Только на сервере"),
                ephemeral=True,
            )
            return

        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Недостаточно прав",
                    config_denied_reason(config.config_role_id),
                ),
                ephemeral=True,
            )
            return

        view = ConfigPanel(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=config_overview_embed(config),
            view=view,
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(ConfigCog(bot))
