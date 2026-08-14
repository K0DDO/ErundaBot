"""Telegram channel slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import error_embed, success_embed
from bot.utils.permissions import is_guild_admin
from bot.views.tgk_views import TgkAddModal, refresh_tgk_board

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class TgkCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._board_synced = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._board_synced:
            return
        self._board_synced = True
        for guild in self.bot.guilds:
            try:
                await self.bot.tgk_service.sync_board(guild, self.bot)
            except Exception:
                log.exception("Failed to sync TGK board for guild %s", guild.id)

    tgk = app_commands.Group(name="tgk", description="Телеграм-каналы участников")

    @tgk.command(name="add", description="Добавить свой ТГК")
    @app_commands.guild_only()
    async def tgk_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TgkAddModal(self.bot))

    @tgk.command(name="list", description="Список ТГК участников")
    @app_commands.guild_only()
    async def tgk_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if config.tgk_channel_id:
            await interaction.response.defer(ephemeral=True)
            message = await self.bot.tgk_service.sync_board(interaction.guild, self.bot)
            if message is None:
                await interaction.followup.send(
                    embed=error_embed("Канал ТГК недоступен"),
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                embed=success_embed("Доска ТГК", f"[Открыть]({message.jump_url})"),
                ephemeral=True,
            )
            return
        channels = await self.bot.tgk_service.list_all(interaction.guild.id)
        view = self.bot.tgk_service.build_board(interaction.guild, channels)
        await interaction.response.send_message(view=view)

    @tgk.command(name="remove", description="Удалить ТГК по номеру")
    @app_commands.describe(number="Номер с доски")
    @app_commands.guild_only()
    async def tgk_remove(self, interaction: discord.Interaction, number: int) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        try:
            channel = await self.bot.tgk_service.remove(
                interaction.guild.id,
                number,
                interaction.user.id,
                is_admin=is_guild_admin(interaction.user),
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_tgk_board(self.bot, interaction.guild)
        await interaction.response.send_message(
            embed=success_embed(f"ТГК #{channel.number} удалён"),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(TgkCog(bot))
