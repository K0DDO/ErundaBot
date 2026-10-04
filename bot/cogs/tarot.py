"""Tarot slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.services.tarot_cards import TarotCard
from bot.services.tarot_service import TarotDeckEmpty
from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)

TAROT_COLOR = 0x9B59B6


class TarotCardView(ui.LayoutView):
    def __init__(
        self,
        card: TarotCard,
        reversed_: bool,
        filename: str,
        *,
        show_meaning: bool,
    ) -> None:
        super().__init__(timeout=None)
        container = ui.Container(accent_color=TAROT_COLOR)
        title = f"## 🔮 {card.title}"
        if reversed_:
            title += "\n-# перевёрнутая"
        container.add_item(ui.TextDisplay(title))
        if show_meaning:
            meaning = card.reversed if reversed_ else card.upright
            container.add_item(ui.TextDisplay(meaning))
        container.add_item(
            ui.MediaGallery(
                discord.MediaGalleryItem(f"attachment://{filename}", description=card.title[:256])
            )
        )
        self.add_item(container)


class TarotCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    @app_commands.command(name="tarot", description="Вытянуть карту таро")
    @app_commands.guild_only()
    async def tarot(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            card, reversed_ = await self.bot.tarot_service.draw(
                interaction.guild.id,
                interaction.user.id,
                config.tarot_reset_minutes,
            )
        except TarotDeckEmpty as exc:
            await interaction.response.send_message(
                embed=error_embed(
                    "Карты закончились",
                    f"Перемешай колоду через `/tarot-shuffle` или подожди {exc.minutes_left} мин.",
                ),
                ephemeral=True,
            )
            return
        path = self.bot.tarot_service.image_path(card, reversed_)
        view = TarotCardView(
            card,
            reversed_,
            path.name,
            show_meaning=config.tarot_show_meaning,
        )
        await interaction.response.send_message(
            view=view,
            file=discord.File(path, filename=path.name),
        )

    @app_commands.command(name="tarot-shuffle", description="Перемешать свою колоду таро")
    @app_commands.guild_only()
    async def tarot_shuffle(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await self.bot.tarot_service.shuffle(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(
            embed=success_embed("Колода перемешана"),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(TarotCog(bot))
