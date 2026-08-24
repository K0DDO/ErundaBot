"""Event slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.utils.formatting import role_mention
from bot.views.event_views import (
    EventCancelConfirmView,
    EventCreateModal,
    event_embed,
    resync_event_cards,
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

    @event.command(name="list", description="Список ивентов")
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        events = await self.bot.event_service.list_scheduled(interaction.guild.id)
        if not events:
            await interaction.response.send_message(
                embed=base_embed(title="Ивенты", description="Пока нет запланированных ивентов."),
                ephemeral=True,
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        lines: list[str] = []
        for ev in events[:15]:
            date_label, time_label = self.bot.event_service.format_starts_at(ev, config.timezone)
            status = "идёт" if self.bot.event_service.has_started(ev) else f"{date_label} {time_label}"
            line = f"**#{ev.number}** {ev.title} — {status} · {role_mention(ev.ping_role_id)}"
            channel_id = ev.channel_id or config.events_channel_id
            if channel_id and ev.message_id:
                url = f"https://discord.com/channels/{ev.guild_id}/{channel_id}/{ev.message_id}"
                line += f" — [открыть]({url})"
            lines.append(line)
        await interaction.response.send_message(
            embed=base_embed(title="Ивенты", description="\n".join(lines)),
            ephemeral=True,
        )

    @event.command(name="ping", description="Пингануть роль ивента")
    @app_commands.describe(number="Номер ивента из /event list")
    @app_commands.guild_only()
    async def event_ping(self, interaction: discord.Interaction, number: int) -> None:
        if interaction.guild is None:
            return
        event = await self.bot.event_service.get_by_number(interaction.guild.id, number)
        if event is None:
            await interaction.response.send_message(
                embed=error_embed("Ивент не найден"),
                ephemeral=True,
            )
            return
        if event.ping_role_id is None:
            await interaction.response.send_message(
                embed=error_embed("У ивента не задана роль для пинга"),
                ephemeral=True,
            )
            return
        role = interaction.guild.get_role(event.ping_role_id)
        if role is None:
            await interaction.response.send_message(
                embed=error_embed("Роль ивента не найдена на сервере"),
                ephemeral=True,
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        channel_id = event.channel_id or config.events_channel_id or interaction.channel_id
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                embed=error_embed("Некуда отправить пинг"),
                ephemeral=True,
            )
            return
        await channel.send(self.bot.event_service.ping_text(event, role))
        await interaction.response.send_message(
            embed=success_embed("Пинг отправлен"),
            ephemeral=True,
        )

    @event_ping.autocomplete("number")
    async def event_ping_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        events = await self.bot.event_service.list_scheduled(interaction.guild.id)
        choices: list[app_commands.Choice[int]] = []
        needle = current.strip().lower()
        for event in events:
            label = f"#{event.number} · {event.title}"
            if self.bot.event_service.has_started(event):
                label += " · идёт"
            if needle and needle not in label.lower() and needle not in str(event.number):
                continue
            choices.append(app_commands.Choice(name=label[:100], value=event.number))
            if len(choices) >= 25:
                break
        return choices

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
        config = await self.bot.config_service.get(interaction.guild.id)
        embed = event_embed(self.bot, event, config.timezone)
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
        return await self.event_ping_autocomplete(interaction, current)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(EventsCog(bot))
