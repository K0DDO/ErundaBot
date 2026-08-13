"""Quote compose and edit modals."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.database.models import Quote
from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class QuoteComposeModal(ui.Modal):
    text = ui.TextInput(
        label="Текст цитаты",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
        placeholder="Можно писать в несколько строк",
    )
    name = ui.TextInput(
        label="Имя автора",
        required=False,
        max_length=80,
        placeholder="Если не выбран участник",
    )
    date = ui.TextInput(
        label="Дата (ДД.ММ.ГГГГ)",
        required=False,
        max_length=10,
        placeholder="необязательно",
    )

    def __init__(
        self,
        bot: ErundaBot,
        *,
        author: discord.Member | None,
        silent: bool,
    ) -> None:
        super().__init__(title="Импорт цитаты" if silent else "Новая цитата")
        self.bot = bot
        self.author = author
        self.silent = silent
        if author is not None:
            self.name.default = author.display_name[:80]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        display = str(self.name.value).strip() or None
        author_id = self.author.id if self.author else 0
        if self.author is not None and display is None:
            display = self.author.display_name
        if author_id == 0 and not display:
            await interaction.response.send_message(
                embed=error_embed("Укажи имя автора или выбери участника"),
                ephemeral=True,
            )
            return

        created_at: str | None = None
        date_value = str(self.date.value).strip()
        if date_value:
            try:
                created_at = self.bot.quote_service.parse_date(date_value)
            except ValueError as exc:
                await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
                return

        try:
            quote = await self.bot.quote_service.add_text(
                interaction.guild.id,
                str(self.text.value),
                interaction.user.id,
                author_id=author_id,
                author_display=display,
                created_at=created_at,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return

        if self.silent:
            await interaction.response.send_message(
                embed=success_embed(
                    "Цитата сохранена",
                    f"#{quote.number} добавлена в базу без отправки в канал.",
                ),
                ephemeral=True,
            )
            return

        config = await self.bot.config_service.get(interaction.guild.id)
        if config.quotes_channel_id:
            channel = interaction.guild.get_channel(config.quotes_channel_id)
            if isinstance(channel, discord.TextChannel):
                await self.bot.quote_service.publish_to_channel(interaction.guild, quote, channel)
                await interaction.response.send_message(
                    embed=success_embed("Цитата сохранена", f"Опубликовано в {channel.mention}"),
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            embed=self.bot.quote_service.format_quote_embed(quote, interaction.guild),
        )


class QuoteEditModal(ui.Modal, title="Изменить цитату"):
    text = ui.TextInput(
        label="Текст цитаты",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )
    name = ui.TextInput(
        label="Имя автора",
        required=False,
        max_length=80,
    )
    date = ui.TextInput(
        label="Дата (ДД.ММ.ГГГГ)",
        required=False,
        max_length=10,
        placeholder="необязательно",
    )

    def __init__(
        self,
        bot: ErundaBot,
        quote: Quote,
        author: discord.Member | None,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.quote = quote
        self.author = author
        self.text.default = quote.content[:2000]
        self.name.default = (quote.author_display or "")[:80]
        if quote.created_at:
            try:
                parsed = datetime.fromisoformat(quote.created_at.replace("Z", "+00:00"))
                self.date.default = parsed.strftime("%d.%m.%Y")
            except ValueError:
                pass

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return

        display = str(self.name.value).strip() or None
        update_author_id = self.author is not None
        update_author_display = display is not None or self.author is not None
        author_id = self.author.id if self.author else None
        if self.author is not None and display is None:
            display = self.author.display_name

        created_at: str | None = None
        date_value = str(self.date.value).strip()
        if date_value:
            try:
                created_at = self.bot.quote_service.parse_date(date_value)
            except ValueError as exc:
                await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
                return

        try:
            quote = await self.bot.quote_service.update(
                interaction.guild.id,
                self.quote.number,
                interaction.user,
                content=str(self.text.value),
                author_id=author_id,
                author_display=display,
                update_author_id=update_author_id,
                update_author_display=update_author_display,
                created_at=created_at,
            )
            await self.bot.quote_service.sync_posted_message(interaction.guild, quote)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self.bot.quote_service.format_quote_embed(quote, interaction.guild),
        )
