"""Personal role commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import error_embed, success_embed
from bot.views.role_views import MyRoleModal

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class RolesCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    myrole = app_commands.Group(name="myrole", description="Персональная роль")

    @myrole.command(name="edit", description="Создать или изменить свою роль")
    @app_commands.guild_only()
    async def myrole_edit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not config.personal_roles_enabled:
            await interaction.response.send_message(
                embed=error_embed("Персональные роли отключены администратором"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(MyRoleModal(self.bot, interaction.user))

    @myrole.command(name="delete", description="Удалить свою персональную роль")
    @app_commands.guild_only()
    async def myrole_delete(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        try:
            await self.bot.role_service.delete_personal_role(
                interaction.guild,
                interaction.user,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("Персональная роль удалена"),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.bot.db.delete_custom_role_record(role.guild.id, role.id)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(RolesCog(bot))
