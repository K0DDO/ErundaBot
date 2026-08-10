"""Interactive /top UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.services.statistics_service import (
    CATEGORY_LABELS,
    PERIOD_LABELS,
    StatCategory,
    StatPeriod,
)
from bot.utils.embeds import base_embed, error_embed
from bot.utils.formatting import format_duration

if TYPE_CHECKING:
    from bot.bot import ErundaBot

MEDALS = ("🥇", "🥈", "🥉")


def format_top_value(category: StatCategory, value: int) -> str:
    if category == StatCategory.VOICE:
        return format_duration(value)
    if category == StatCategory.OVERALL:
        return str(value)
    return str(value)


class TopView(discord.ui.View):
    def __init__(
        self,
        bot: ErundaBot,
        guild_id: int,
        tz_name: str,
        *,
        category: StatCategory = StatCategory.MESSAGES,
        period: StatPeriod = StatPeriod.MONTH,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.tz_name = tz_name
        self.category = category
        self.period = period

        self.category_select = discord.ui.Select(
            placeholder="Категория",
            options=[
                discord.SelectOption(
                    label=CATEGORY_LABELS[StatCategory.MESSAGES],
                    value=StatCategory.MESSAGES.value,
                    emoji="💬",
                    default=category == StatCategory.MESSAGES,
                ),
                discord.SelectOption(
                    label=CATEGORY_LABELS[StatCategory.VOICE],
                    value=StatCategory.VOICE.value,
                    emoji="🎤",
                    default=category == StatCategory.VOICE,
                ),
                discord.SelectOption(
                    label=CATEGORY_LABELS[StatCategory.REACTIONS],
                    value=StatCategory.REACTIONS.value,
                    emoji="❤️",
                    default=category == StatCategory.REACTIONS,
                ),
                discord.SelectOption(
                    label=CATEGORY_LABELS[StatCategory.OVERALL],
                    value=StatCategory.OVERALL.value,
                    emoji="📊",
                    default=category == StatCategory.OVERALL,
                ),
            ],
        )
        self.category_select.callback = self.on_category
        self.add_item(self.category_select)

        self.period_select = discord.ui.Select(
            placeholder="Период",
            options=[
                discord.SelectOption(
                    label=PERIOD_LABELS[StatPeriod.TODAY],
                    value=StatPeriod.TODAY.value,
                    default=period == StatPeriod.TODAY,
                ),
                discord.SelectOption(
                    label=PERIOD_LABELS[StatPeriod.WEEK],
                    value=StatPeriod.WEEK.value,
                    default=period == StatPeriod.WEEK,
                ),
                discord.SelectOption(
                    label=PERIOD_LABELS[StatPeriod.MONTH],
                    value=StatPeriod.MONTH.value,
                    default=period == StatPeriod.MONTH,
                ),
                discord.SelectOption(
                    label=PERIOD_LABELS[StatPeriod.ALL],
                    value=StatPeriod.ALL.value,
                    default=period == StatPeriod.ALL,
                ),
            ],
        )
        self.period_select.callback = self.on_period
        self.add_item(self.period_select)

    async def on_category(self, interaction: discord.Interaction) -> None:
        self.category = StatCategory(self.category_select.values[0])
        await self.refresh(interaction)

    async def on_period(self, interaction: discord.Interaction) -> None:
        self.period = StatPeriod(self.period_select.values[0])
        await self.refresh(interaction)

    async def build_embed(self) -> discord.Embed:
        entries = await self.bot.statistics_service.get_top(
            self.guild_id,
            self.category,
            self.period,
            self.tz_name,
            limit=10,
        )
        period_title = self.bot.statistics_service.period_title(self.period, self.tz_name)
        cat = CATEGORY_LABELS[self.category]
        title = f"Топ — {cat} — {period_title}"

        if not entries:
            return base_embed(title=title, description="Пока нет данных.")

        lines: list[str] = []
        for index, entry in enumerate(entries):
            medal = MEDALS[index] if index < 3 else f"`#{index + 1}`"
            value = format_top_value(self.category, entry.value)
            lines.append(f"{medal} <@{entry.user_id}> — {value}")
        return base_embed(title=title, description="\n".join(lines))

    async def refresh(self, interaction: discord.Interaction) -> None:
        # Rebuild selects with new defaults.
        new_view = TopView(
            self.bot,
            self.guild_id,
            self.tz_name,
            category=self.category,
            period=self.period,
        )
        embed = await new_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=new_view)
