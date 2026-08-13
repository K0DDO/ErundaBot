"""Birthday UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.services.birthday_service import format_birthday_date
from bot.utils.birthday_emojis import resolve_birthday_emoji
from bot.utils.embeds import BRAND_COLOR, error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


async def refresh_birthday_board(bot: ErundaBot, guild: discord.Guild | None) -> None:
    if guild is None:
        return
    try:
        await bot.birthday_service.sync_board(guild, bot)
    except Exception:
        pass


class BirthdayHintLabel(ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="← это кнопка, жми",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        pass


class BirthdayAddButton(ui.Button):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(label="Добавить ДР", emoji="🎂", style=discord.ButtonStyle.secondary)
        self.bot = bot
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            BirthdaySetModal(self.bot, self.guild_id, interaction.user.id)
        )


class BirthdayListView(ui.LayoutView):
    def __init__(
        self,
        bot: ErundaBot,
        guild_id: int,
        text: str,
    ) -> None:
        super().__init__(timeout=None)
        container = ui.Container(accent_color=BRAND_COLOR)
        container.add_item(ui.TextDisplay(text))
        row = ui.ActionRow()
        row.add_item(BirthdayAddButton(bot, guild_id))
        row.add_item(BirthdayHintLabel())
        container.add_item(row)
        self.add_item(container)


class BirthdayPreviewView(BirthdayListView):
    """Ephemeral preview uses the same layout as the channel board."""


class BirthdaySetModal(discord.ui.Modal, title="Указать / изменить день рождения"):
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
    emoji = discord.ui.TextInput(
        label="Эмодзи перед именем",
        placeholder=":cristo: или 🎂",
        required=False,
        max_length=64,
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
            emoji_raw = str(self.emoji.value).strip() if self.emoji.value else ""
            emoji = resolve_birthday_emoji(
                interaction.guild,
                emoji_raw if emoji_raw else "🎂",
                user_id=self.user_id,
            )
            birthday = await self.bot.birthday_service.set_birthday(
                self.guild_id,
                self.user_id,
                day,
                month,
                year,
                emoji=emoji,
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
                f"{birthday.emoji} {format_birthday_date(birthday.day, birthday.month, birthday.year)}",
            ),
            ephemeral=True,
        )
        await refresh_birthday_board(self.bot, interaction.guild)
