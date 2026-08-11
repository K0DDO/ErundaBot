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
    rgb = discord.ui.TextInput(
        label="RGB (on/off)",
        required=False,
        max_length=3,
        placeholder="off",
    )
    speed = discord.ui.TextInput(
        label="Скорость RGB (0.1–5)",
        required=False,
        max_length=4,
        placeholder="1",
    )

    def __init__(self, bot: ErundaBot, member: discord.Member) -> None:
        super().__init__()
        self.bot = bot
        self.member = member

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        guild = interaction.guild
        bot_member = guild.me
        if bot_member is None:
            return
        try:
            color = self.bot.role_service.parse_color(str(self.color.value or ""))
            rgb_raw = str(self.rgb.value or "").strip().lower()
            rgb_enabled = None
            if rgb_raw in ("on", "1", "yes", "да", "вкл"):
                rgb_enabled = True
            elif rgb_raw in ("off", "0", "no", "нет", "выкл"):
                rgb_enabled = False
            speed_raw = str(self.speed.value or "").strip()
            rgb_speed = float(speed_raw) if speed_raw else None
            name = str(self.name.value).strip() if self.name.value else None
            role, record = await self.bot.role_service.update_personal_role(
                guild,
                self.member,
                bot_member,
                name=name,
                color=color,
                rgb_enabled=rgb_enabled,
                rgb_speed=rgb_speed,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        rgb_label = "вкл" if record.rgb_enabled else "выкл"
        await interaction.response.send_message(
            embed=success_embed(
                "Роль обновлена",
                f"{role.mention}\nRGB: {rgb_label}",
            ),
            ephemeral=True,
        )
