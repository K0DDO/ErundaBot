"""Birthday slash commands."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.models import Birthday
from bot.utils.embeds import error_embed, success_embed
from bot.utils.permissions import can_edit_config, config_denied_reason
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

    @birthday.command(name="test", description="Тестовое поздравление (видно только тебе)")
    @app_commands.describe(user="Кого поздравить, по умолчанию тебя")
    @app_commands.guild_only()
    async def birthday_test(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(config_denied_reason(config.config_role_id)),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        target = user or interaction.user
        today = datetime.now(ZoneInfo(config.timezone)).date()
        stored = await self.bot.birthday_service.get_birthday(interaction.guild.id, target.id)
        birthday = Birthday(
            guild_id=interaction.guild.id,
            user_id=target.id,
            day=today.day,
            month=today.month,
            year=stored.year if stored is not None else None,
        )
        try:
            embed, used_ai = await self.bot.birthday_service.announce_embed(
                interaction.guild,
                birthday,
                today,
                self.bot.ai_service,
            )
        except Exception:
            log.exception("Birthday test greeting failed")
            await interaction.followup.send(
                embed=error_embed("Не получилось собрать поздравление"),
                ephemeral=True,
            )
            return
        note = "Сгенерировано ИИ" if used_ai else "ИИ не ответил — запасной текст"
        embed.set_footer(text=f"Тест · {note}")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
