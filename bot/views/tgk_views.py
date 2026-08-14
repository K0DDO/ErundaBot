"""Telegram channel UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


async def refresh_tgk_board(bot: ErundaBot, guild: discord.Guild | None) -> None:
    if guild is None:
        return
    try:
        await bot.tgk_service.sync_board(guild, bot)
    except Exception:
        pass


class TgkAddModal(discord.ui.Modal, title="Добавить ТГК"):
    title_input = discord.ui.TextInput(label="Название", max_length=80, required=True)
    link = discord.ui.TextInput(
        label="Ссылка",
        placeholder="https://t.me/channel или @channel",
        max_length=120,
        required=True,
    )

    def __init__(self, bot: ErundaBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            channel = await self.bot.tgk_service.add(
                interaction.guild.id,
                interaction.user.id,
                str(self.title_input.value),
                str(self.link.value),
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_tgk_board(self.bot, interaction.guild)
        extra = "Картинка подтянулась." if channel.image_url else "Картинку взять не удалось — оставил без неё."
        await interaction.response.send_message(
            embed=success_embed(f"ТГК #{channel.number} добавлен", extra),
            ephemeral=True,
        )
