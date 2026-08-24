"""Birthday slash commands."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import error_embed, success_embed
from bot.views.birthday_views import BirthdaySetModal, refresh_birthday_board

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class BirthdaysCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._board_synced = False
        self._old_greetings_cleaned = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._board_synced:
            return
        self._board_synced = True
        for guild in self.bot.guilds:
            config = await self.bot.config_service.get(guild.id)
            if config.birthday_channel_id:
                try:
                    await self.bot.birthday_service.sync_board(guild, self.bot)
                except Exception:
                    log.exception("Failed to sync birthday board for guild %s", guild.id)
                try:
                    local_today = datetime.now(ZoneInfo(config.timezone)).date()
                    removed = await self.bot.birthday_service.cleanup_past_messages(
                        guild,
                        local_today,
                        history_limit=500,
                    )
                    if removed:
                        log.info(
                            "Removed %s old birthday greetings in guild %s",
                            removed,
                            guild.id,
                        )
                except Exception:
                    log.exception(
                        "Failed to cleanup old birthday greetings for guild %s",
                        guild.id,
                    )
        self._old_greetings_cleaned = True

    birthday = app_commands.Group(name="birthday", description="Дни рождения")

    @birthday.command(name="set", description="Указать / изменить дату")
    @app_commands.guild_only()
    async def birthday_set(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_modal(
            BirthdaySetModal(self.bot, interaction.guild.id, interaction.user.id)
        )

    @birthday.command(name="remove", description="Удалить свой день рождения")
    @app_commands.guild_only()
    async def birthday_remove(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        removed = await self.bot.birthday_service.remove_birthday(
            interaction.guild.id,
            interaction.user.id,
        )
        if not removed:
            await interaction.response.send_message(
                embed=error_embed("День рождения не найден"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed("День рождения удалён"),
            ephemeral=True,
        )
        await refresh_birthday_board(self.bot, interaction.guild)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
