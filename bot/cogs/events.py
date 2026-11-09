"""Event slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import error_embed, success_embed
from bot.views.event_views import (
    EventCancelConfirmView,
    EventCreateModal,
    register_event_views,
    render_event_embed,
    resync_event_cards,
    retire_event,
    sweep_orphan_event_mentions,
)

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class EventsCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._cards_synced = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._cards_synced:
            return
        self._cards_synced = True
        try:
            events = await self.bot.db.list_scheduled_events()
            register_event_views(self.bot, events)
            await resync_event_cards(self.bot, events)
            await sweep_orphan_event_mentions(self.bot)
            log.info("Resynced %s event cards", len(events))
        except Exception:
            log.exception("Failed to resync event cards")

    event = app_commands.Group(name="event", description="Ивенты")

    @event.command(name="create", description="Создать ивент")
    @app_commands.describe(role="Роль, которую будут пинговать для этого ивента")
    @app_commands.guild_only()
    async def event_create(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        if interaction.guild is None:
            return
        if role.is_default() or role.is_integration() or role.managed:
            await interaction.response.send_message(
                embed=error_embed("Выбери обычную роль сервера"),
                ephemeral=True,
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        await interaction.response.send_modal(
            EventCreateModal(
                self.bot,
                interaction.guild.id,
                interaction.user.id,
                config.timezone,
                ping_role_id=role.id,
            )
        )

    async def _event_number_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
        events: list,
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        config = await self.bot.config_service.get(interaction.guild.id)
        choices: list[app_commands.Choice[int]] = []
        needle = current.strip().lower()
        for event in events:
            date_label, time_label = self.bot.event_service.format_starts_at(
                event, config.timezone
            )
            label = f"#{event.number} · {date_label} {time_label} · {event.title}"
            if self.bot.event_service.has_started(event):
                label += " · идёт"
            if needle and needle not in label.lower() and needle not in str(event.number):
                continue
            choices.append(app_commands.Choice(name=label[:100], value=event.number))
            if len(choices) >= 25:
                break
        return choices

    @event.command(name="cancel", description="Отменить ивент")
    @app_commands.describe(number="Номер ивента")
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
        config = await self.bot.config_service.get(interaction.guild.id)
        embed = await render_event_embed(self.bot, event, config.timezone)
        await interaction.response.send_message(
            content="**Удалить этот ивент?**",
            embed=embed,
            view=EventCancelConfirmView(self.bot, event.id, interaction.user.id),
            ephemeral=True,
        )

    @event_cancel.autocomplete("number")
    async def event_cancel_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        events = await self.bot.event_service.list_scheduled(interaction.guild.id)
        return await self._event_number_autocomplete(interaction, current, events)

    @event.command(name="ended", description="Завершить ивент")
    @app_commands.describe(number="Номер ивента")
    @app_commands.guild_only()
    async def event_ended(self, interaction: discord.Interaction, number: int) -> None:
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
            event = await self.bot.event_service.end(match.id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await retire_event(self.bot, event, status="completed")
        await interaction.followup.send(embed=success_embed("Ивент завершён"), ephemeral=True)

    @event_ended.autocomplete("number")
    async def event_ended_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        config = await self.bot.config_service.get(interaction.guild.id)
        events = await self.bot.event_service.list_running(interaction.guild.id)
        choices: list[app_commands.Choice[int]] = []
        needle = current.strip().lower()
        for event in events:
            date_label, time_label = self.bot.event_service.format_starts_at(
                event, config.timezone
            )
            label = f"#{event.number} · {date_label} {time_label} · {event.title} · идёт"
            if needle and needle not in label.lower() and needle not in str(event.number):
                continue
            choices.append(app_commands.Choice(name=label[:100], value=event.number))
            if len(choices) >= 25:
                break
        return choices


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(EventsCog(bot))
