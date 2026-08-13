"""Quote compose, edit, and card UI."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.database.models import Quote
from bot.utils.embeds import BRAND_COLOR, error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class QuoteCardView(ui.LayoutView):
    def __init__(
        self,
        *,
        number_text: str,
        quote_text: str,
        author_text: str,
        date_text: str | None = None,
        reactions_text: str = "",
        accent_color: int = BRAND_COLOR,
        avatar_url: str | None = None,
        avatar_description: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        container = ui.Container(accent_color=accent_color)
        container.add_item(ui.TextDisplay(number_text))
        container.add_item(ui.TextDisplay(quote_text))

        author_items = [ui.TextDisplay(author_text)]
        if date_text:
            author_items.append(ui.TextDisplay(date_text))

        if avatar_url:
            container.add_item(
                ui.Section(
                    *author_items,
                    accessory=ui.Thumbnail(
                        media=avatar_url,
                        description=(avatar_description or "автор")[:256],
                    ),
                )
            )
        else:
            meta = author_text if not date_text else f"{author_text}\n{date_text}"
            container.add_item(ui.TextDisplay(meta))

        if reactions_text:
            container.add_item(ui.TextDisplay(reactions_text))
        self.add_item(container)


class QuoteComposeModal(ui.Modal):
    text = ui.TextInput(
        label="Текст цитаты",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
        placeholder="Можно писать в несколько строк",
    )
    name = ui.TextInput(
        label="Имя на карточке",
        required=True,
        max_length=80,
        placeholder="Обязательно, так будет подписана цитата",
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
        authors: list[discord.Member],
        silent: bool,
    ) -> None:
        super().__init__(title="Импорт цитаты" if silent else "Новая цитата")
        self.bot = bot
        self.authors = authors
        self.silent = silent
        if authors:
            self.name.default = authors[0].display_name[:80]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        display = str(self.name.value).strip()
        if not display:
            await interaction.response.send_message(
                embed=error_embed("Укажи имя на карточке"),
                ephemeral=True,
            )
            return
        author_ids = [member.id for member in self.authors]
        author_id = author_ids[0] if author_ids else 0

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
                author_ids=author_ids,
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
            view=self.bot.quote_service.build_quote_card(quote, interaction.guild),
        )


class QuoteEditModal(ui.Modal, title="Изменить цитату"):
    text = ui.TextInput(
        label="Текст цитаты",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )
    name = ui.TextInput(
        label="Имя на карточке",
        required=True,
        max_length=80,
        placeholder="Обязательно, так будет подписана цитата",
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
        authors: list[discord.Member],
    ) -> None:
        super().__init__()
        self.bot = bot
        self.quote = quote
        self.authors = authors
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

        display = str(self.name.value).strip()
        if not display:
            await interaction.response.send_message(
                embed=error_embed("Укажи имя на карточке"),
                ephemeral=True,
            )
            return
        author_ids = [member.id for member in self.authors]
        update_author_id = bool(author_ids)
        author_id = author_ids[0] if author_ids else None

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
                update_author_display=True,
                created_at=created_at,
                author_ids=author_ids,
            )
            await self.bot.quote_service.sync_posted_message(interaction.guild, quote)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            view=self.bot.quote_service.build_quote_card(quote, interaction.guild),
        )
