"""Role UI modals."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class MyRoleModal(discord.ui.Modal, title="Моя роль"):
    name = discord.ui.TextInput(label="Название", required=False, max_length=100)
    color = discord.ui.TextInput(label="Цвет (#RRGGBB)", required=False, max_length=7, placeholder="#7C9CFF")

    def __init__(self, bot: ErundaBot, member: discord.Member) -> None:
        super().__init__()
        self.bot = bot
        self.member = member

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        guild = interaction.guild
        try:
            color = self.bot.role_service.parse_color(str(self.color.value or ""))
            name = str(self.name.value).strip() if self.name.value else None
            role, _record = await self.bot.role_service.update_personal_role(
                guild,
                self.member,
                name=name,
                color=color,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("Роль обновлена", f"**{role.name}**"),
            ephemeral=True,
        )
