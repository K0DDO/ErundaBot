"""Telegram channel UI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


async def refresh_tgk_board(bot: ErundaBot, guild: discord.Guild | None) -> None:
    if guild is None:
        return
    try:
        await bot.tgk_service.sync_board(guild, bot)
    except Exception:
        log.exception("Failed to sync TGK board for guild %s", guild.id)


class TgkAddModal(discord.ui.Modal, title="Добавить ТГК"):
    link = discord.ui.TextInput(
        label="Ссылка",
        placeholder="https://t.me/channel, @channel или https://t.me/+invite",
        max_length=120,
        required=True,
    )

    def __init__(self, bot: ErundaBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            channel = await self.bot.tgk_service.add(
                interaction.guild.id,
                interaction.user.id,
                str(self.link.value),
            )
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_tgk_board(self.bot, interaction.guild)
        parts = [f"**{channel.title}**"]
        if channel.image_url:
            parts.append("Картинка подтянулась.")
        else:
            parts.append("Название взял с t.me, картинку получить не удалось.")
        await interaction.followup.send(
            embed=success_embed(f"ТГК #{channel.number} добавлен", "\n".join(parts)),
            ephemeral=True,
        )
