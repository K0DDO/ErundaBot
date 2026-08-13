"""Birthday slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.birthday_service import format_birthday_date, member_display
from bot.utils.embeds import base_embed, error_embed, success_embed
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
                    await self.bot.birthday_service.sync_board(guild)
                except Exception:
                    log.exception("Failed to sync birthday board for guild %s", guild.id)

    birthday = app_commands.Group(name="birthday", description="Дни рождения")

    @birthday.command(name="set", description="Указать свой день рождения")
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

    @birthday.command(name="list", description="Список дней рождения на сервере")
    @app_commands.guild_only()
    async def birthday_list(self, interaction: discord.Interaction) -> None:
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

        lines: list[str] = []
        for entry in entries[:25]:
            bday = entry.birthday
            when = format_birthday_date(bday.day, bday.month)
            name = member_display(interaction.guild, bday.user_id)
            if entry.days_until == 0:
                suffix = "сегодня"
            elif entry.days_until == 1:
                suffix = "завтра"
            else:
                suffix = f"через {entry.days_until} дн."
            lines.append(f"**{name}** — {when} ({suffix})")

        more = ""
        if len(entries) > 25:
            more = f"\n…и ещё {len(entries) - 25}"

        await interaction.response.send_message(
            embed=base_embed(
                title="Дни рождения",
                description="\n".join(lines) + more,
            ),
            ephemeral=True,
        )

    @birthday.command(name="next", description="Ближайший день рождения")
    @app_commands.guild_only()
    async def birthday_next(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        config = await self.bot.config_service.get(interaction.guild.id)
        entry = await self.bot.birthday_service.next_birthday(
            interaction.guild.id,
            config.timezone,
        )
        if entry is None:
            await interaction.response.send_message(
                embed=base_embed(
                    title="Ближайший день рождения",
                    description="Список пуст.",
                ),
                ephemeral=True,
            )
            return

        bday = entry.birthday
        when = format_birthday_date(bday.day, bday.month)
        name = member_display(interaction.guild, bday.user_id)
        if entry.days_until == 0:
            timing = "сегодня"
        elif entry.days_until == 1:
            timing = "завтра"
        else:
            timing = f"через {entry.days_until} дн."

        await interaction.response.send_message(
            embed=base_embed(
                title="Ближайший день рождения",
                description=f"**{name}** — {when} ({timing})",
            ),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
