"""Event slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.views.event_views import EventCreateModal, register_event_views, resync_event_cards, retire_event

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class EventsCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._views_restored = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._views_restored:
            return
        self._views_restored = True
        try:
            events = await self.bot.db.list_scheduled_events()
            register_event_views(self.bot, events)
            await resync_event_cards(self.bot, events)
            log.info("Restored %s event views", len(events))
        except Exception:
            log.exception("Failed to restore event views")

    event = app_commands.Group(name="event", description="Ивенты")

    @event.command(name="create", description="Создать ивент")
    @app_commands.guild_only()
    async def event_create(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        await interaction.response.send_modal(
            EventCreateModal(
                self.bot,
                interaction.guild.id,
                interaction.user.id,
                config.timezone,
            )
        )

    @event.command(name="list", description="Список ивентов")
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        events = await self.bot.event_service.list_upcoming(interaction.guild.id)
        if not events:
            await interaction.response.send_message(
                embed=base_embed(title="Ивенты", description="Пока нет запланированных ивентов."),
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        lines: list[str] = []
        for ev in events[:15]:
            date_label, time_label = self.bot.event_service.format_starts_at(ev, config.timezone)
            count = await self.bot.event_service.participant_count(ev.id)
            line = f"**#{ev.number}** {ev.title} — {date_label} {time_label} ({count} чел.)"
            channel_id = ev.channel_id or config.events_channel_id
            if channel_id and ev.message_id:
                url = f"https://discord.com/channels/{ev.guild_id}/{channel_id}/{ev.message_id}"
                line += f" — [открыть]({url})"
            lines.append(line)
        await interaction.response.send_message(
            embed=base_embed(title="Ивенты", description="\n".join(lines)),
        )

    @event.command(name="cancel", description="Отменить ивент (создатель)")
    @app_commands.describe(number="Номер ивента из /event list")
    @app_commands.guild_only()
    async def event_cancel(self, interaction: discord.Interaction, number: int) -> None:
        if interaction.guild is None:
            return
        match = await self.bot.event_service.get_by_number(interaction.guild.id, number)
        if match is None:
            await interaction.response.send_message(
                embed=error_embed("Ивент не найден"),
                ephemeral=True,
            )
            return
        try:
            event = await self.bot.event_service.cancel(match.id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.defer()
        await retire_event(self.bot, event, status="cancelled")
        await interaction.followup.send(embed=success_embed("Ивент отменён"))


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(EventsCog(bot))
