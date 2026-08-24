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
from bot.services.birthday_service import occurrence_on_year
from bot.utils.embeds import error_embed, success_embed, base_embed
from bot.views.birthday_views import BirthdaySetModal, refresh_birthday_board

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class BirthdaysCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._board_synced = False

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

    @birthday.command(name="preview", description="Ближайшие дни рождения")
    @app_commands.guild_only()
    async def birthday_preview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        entries = await self.bot.birthday_service.list_sorted(
            interaction.guild.id,
            config.timezone,
        )
        if not entries:
            await interaction.response.send_message(
                embed=base_embed(
                    title="Дни рождения",
                    description="Пока никто не указал день рождения.",
                ),
                ephemeral=True,
            )
            return
        text = self.bot.birthday_service.format_preview_lines(interaction.guild, entries)
        await interaction.response.send_message(
            embed=base_embed(title="Ближайшие дни рождения", description=text),
            ephemeral=True,
        )

    @birthday.command(name="test-announce", description="Дебаг: ИИ-поздравление только тебе")
    @app_commands.describe(member="Кому сгенерировать, по умолчанию ты")
    @app_commands.guild_only()
    async def birthday_test_announce(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            return
        await interaction.response.defer(ephemeral=True)
        config = await self.bot.config_service.get(interaction.guild.id)
        local_today = datetime.now(ZoneInfo(config.timezone)).date()
        birthday = await self.bot.birthday_service.get_birthday(
            interaction.guild.id,
            target.id,
        )
        if birthday is None:
            birthday = Birthday(
                guild_id=interaction.guild.id,
                user_id=target.id,
                day=local_today.day,
                month=local_today.month,
            )
            announce_on = local_today
        else:
            announce_on = occurrence_on_year(birthday.day, birthday.month, local_today.year)
        embed, used_ai = await self.bot.birthday_service.announce_embed(
            interaction.guild,
            birthday,
            announce_on,
            self.bot.ai_service,
            mention=False,
        )
        note = None if used_ai else "ИИ не ответил за 2 минуты — запасной текст"
        await interaction.followup.send(
            content=f"{target.mention}" + (f"\n{note}" if note else ""),
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
