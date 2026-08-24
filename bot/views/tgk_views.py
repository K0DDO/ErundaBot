"""Telegram channel UI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.utils.embeds import BRAND_COLOR, error_embed, success_embed

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


def bind_tgk_board_view(bot: ErundaBot, guild_id: int, message_id: int) -> None:
    bot.add_view(TgkBoardView(bot, guild_id), message_id=message_id)


def append_tgk_board_actions(view: ui.LayoutView, bot: ErundaBot, guild_id: int) -> None:
    actions = ui.Container(accent_color=BRAND_COLOR)
    row = ui.ActionRow()
    row.add_item(TgkAddButton(bot, guild_id))
    row.add_item(TgkRemoveButton(bot, guild_id))
    actions.add_item(row)
    view.add_item(actions)


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


class TgkRemoveModal(discord.ui.Modal, title="Убрать ТГК"):
    number = discord.ui.TextInput(
        label="Номер с доски",
        placeholder="3",
        max_length=4,
        required=True,
    )

    def __init__(self, bot: ErundaBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        raw = str(self.number.value).strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                embed=error_embed("Номер должен быть числом"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            channel = await self.bot.tgk_service.remove(
                interaction.guild.id,
                int(raw),
                interaction.user.id,
                is_admin=False,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_tgk_board(self.bot, interaction.guild)
        await interaction.followup.send(
            embed=success_embed(f"ТГК #{channel.number} удалён"),
            ephemeral=True,
        )


class TgkAddButton(ui.Button):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(
            label="Добавить ТГК",
            style=discord.ButtonStyle.success,
            custom_id=f"tgk:add:{guild_id}",
        )
        self.bot = bot
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TgkAddModal(self.bot))


class TgkRemoveButton(ui.Button):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(
            label="Убрать ТГК",
            style=discord.ButtonStyle.secondary,
            custom_id=f"tgk:remove:{guild_id}",
        )
        self.bot = bot
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        owned = await self.bot.tgk_service.list_for_user(interaction.guild.id, interaction.user.id)
        if not owned:
            await interaction.response.send_message(
                embed=error_embed("У тебя нет ТГК на доске"),
                ephemeral=True,
            )
            return
        if len(owned) == 1:
            await interaction.response.defer(ephemeral=True)
            try:
                channel = await self.bot.tgk_service.remove(
                    interaction.guild.id,
                    owned[0].number,
                    interaction.user.id,
                    is_admin=False,
                )
            except ValueError as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return
            await refresh_tgk_board(self.bot, interaction.guild)
            await interaction.followup.send(
                embed=success_embed(f"ТГК #{channel.number} удалён"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TgkRemoveModal(self.bot))


class TgkBoardView(discord.ui.View):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.add_item(TgkAddButton(bot, guild_id))
        self.add_item(TgkRemoveButton(bot, guild_id))


async def register_tgk_board_views(bot: ErundaBot) -> None:
    for guild in bot.guilds:
        config = await bot.db.get_guild(guild.id)
        if config is None or config.tgk_channel_id is None:
            continue
        message_ids = await bot.db.get_tgk_board_message_ids(guild.id)
        if message_ids:
            bind_tgk_board_view(bot, guild.id, message_ids[0])
