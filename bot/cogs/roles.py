"""Role management commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import error_embed, success_embed
from bot.utils.permissions import is_guild_admin
from bot.views.role_views import MyRoleModal

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class RolesCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    role = app_commands.Group(name="role", description="Управление ролями (админ)")

    @role.command(name="create", description="Создать роль")
    @app_commands.describe(name="Название", color="Цвет #RRGGBB", rgb="Включить RGB")
    @app_commands.guild_only()
    async def role_create(
        self,
        interaction: discord.Interaction,
        name: str,
        color: str | None = None,
        rgb: bool = False,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not is_guild_admin(interaction.user):
            await interaction.response.send_message(embed=error_embed("Недостаточно прав"), ephemeral=True)
            return
        bot_member = interaction.guild.me
        if bot_member is None:
            return
        try:
            parsed = self.bot.role_service.parse_color(color)
            role, _ = await self.bot.role_service.create_managed_role(
                interaction.guild, bot_member, name, parsed, rgb=rgb
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("Роль создана", role.mention),
        )

    @role.command(name="edit", description="Изменить роль")
    @app_commands.describe(
        role="Роль",
        name="Новое название",
        color="Цвет #RRGGBB",
        rgb="RGB-анимация",
    )
    @app_commands.guild_only()
    async def role_edit(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        name: str | None = None,
        color: str | None = None,
        rgb: bool | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not is_guild_admin(interaction.user):
            await interaction.response.send_message(embed=error_embed("Недостаточно прав"), ephemeral=True)
            return
        bot_member = interaction.guild.me
        if bot_member is None:
            return
        try:
            parsed = self.bot.role_service.parse_color(color) if color else None
            await self.bot.role_service.edit_role(
                interaction.guild,
                bot_member,
                role,
                name=name,
                color=parsed,
                rgb=rgb,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=success_embed("Роль обновлена", role.mention))

    @role.command(name="delete", description="Удалить роль")
    @app_commands.describe(role="Роль")
    @app_commands.guild_only()
    async def role_delete(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not is_guild_admin(interaction.user):
            await interaction.response.send_message(embed=error_embed("Недостаточно прав"), ephemeral=True)
            return
        bot_member = interaction.guild.me
        if bot_member is None:
            return
        try:
            await self.bot.role_service.delete_role(interaction.guild, bot_member, role)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(embed=success_embed("Роль удалена"))

    @app_commands.command(name="myrole", description="Управление персональной ролью")
    @app_commands.guild_only()
    async def myrole(self, interaction: discord.Interaction) -> None:
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

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.bot.db.delete_custom_role_record(role.guild.id, role.id)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(RolesCog(bot))
