"""Birthday UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.services.birthday_service import format_birthday_date
from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


async def clear_birthday_board(bot: ErundaBot, guild: discord.Guild | None) -> None:
    if guild is None:
        return
    try:
        await bot.birthday_service.clear_board(guild)
    except Exception:
        pass


class BirthdaySetModal(discord.ui.Modal, title="Указать день рождения"):
    day = discord.ui.TextInput(
        label="День",
        placeholder="15",
        required=True,
        max_length=2,
    )
    month = discord.ui.TextInput(
        label="Месяц",
        placeholder="8",
        required=True,
        max_length=2,
    )
    year = discord.ui.TextInput(
        label="Год (необязательно)",
        placeholder="2000",
        required=False,
        max_length=4,
    )

    def __init__(self, bot: ErundaBot, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            day = int(str(self.day.value).strip())
            month = int(str(self.month.value).strip())
            year_raw = str(self.year.value).strip() if self.year.value else ""
            year = int(year_raw) if year_raw else None
            birthday = await self.bot.birthday_service.set_birthday(
                self.guild_id,
                self.user_id,
                day,
                month,
                year,
            )
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Не удалось сохранить", str(exc)),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=success_embed(
                "День рождения сохранён",
                format_birthday_date(birthday.day, birthday.month, birthday.year),
            ),
            ephemeral=True,
        )
