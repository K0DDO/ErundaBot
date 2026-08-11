"""Event slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.views.event_views import EventCreateModal, event_embed, register_event_views

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
            log.info("Restored %s event views", len(events))
        except Exception:
            log.exception("Failed to restore event views")

    event = app_commands.Group(name="event", description="Мероприятия")

    @event.command(name="create", description="Создать мероприятие")
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

    @event.command(name="list", description="Список мероприятий")
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        events = await self.bot.event_service.list_scheduled(interaction.guild.id)
        if not events:
            await interaction.response.send_message(
                embed=base_embed(title="Мероприятия", description="Нет запланированных."),
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        lines: list[str] = []
        for ev in events[:15]:
            date_label, time_label = self.bot.event_service.format_starts_at(ev, config.timezone)
            count = await self.bot.event_service.participant_count(ev.id)
            lines.append(f"**#{ev.id}** {ev.title} — {date_label} {time_label} ({count} чел.)")
        await interaction.response.send_message(
            embed=base_embed(title="Мероприятия", description="\n".join(lines)),
        )

    @event.command(name="info", description="Информация о мероприятии")
    @app_commands.describe(event_id="ID мероприятия")
    @app_commands.guild_only()
    async def event_info(self, interaction: discord.Interaction, event_id: int) -> None:
        if interaction.guild is None:
            return
        event = await self.bot.event_service.get(event_id)
        if event is None or event.guild_id != interaction.guild.id:
            await interaction.response.send_message(embed=error_embed("Не найдено"), ephemeral=True)
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        count = await self.bot.event_service.participant_count(event.id)
        await interaction.response.send_message(
            embed=event_embed(self.bot, event, config.timezone, count),
        )

    @event.command(name="join", description="Присоединиться к мероприятию")
    @app_commands.describe(event_id="ID мероприятия")
    @app_commands.guild_only()
    async def event_join(self, interaction: discord.Interaction, event_id: int) -> None:
        try:
            event, count = await self.bot.event_service.join(event_id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        config = await self.bot.config_service.get(event.guild_id)
        await interaction.response.send_message(
            embed=success_embed("Вы участвуете", f"Участников: {count}"),
            ephemeral=True,
        )

    @event.command(name="leave", description="Покинуть мероприятие")
    @app_commands.describe(event_id="ID мероприятия")
    @app_commands.guild_only()
    async def event_leave(self, interaction: discord.Interaction, event_id: int) -> None:
        try:
            event, count = await self.bot.event_service.leave(event_id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("Вы вышли", f"Участников: {count}"),
            ephemeral=True,
        )

    @event.command(name="cancel", description="Отменить мероприятие (организатор)")
    @app_commands.describe(event_id="ID мероприятия")
    @app_commands.guild_only()
    async def event_cancel(self, interaction: discord.Interaction, event_id: int) -> None:
        try:
            event = await self.bot.event_service.cancel(event_id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        config = await self.bot.config_service.get(event.guild_id)
        count = await self.bot.event_service.participant_count(event.id)
        if event.message_id and interaction.guild:
            ch_id = event.channel_id or config.events_channel_id
            channel = interaction.guild.get_channel(ch_id or 0) if ch_id else None
            if channel and hasattr(channel, "fetch_message"):
                try:
                    msg = await channel.fetch_message(event.message_id)
                    embed = event_embed(self.bot, event, config.timezone, count)
                    await msg.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass
        await interaction.response.send_message(embed=success_embed("Мероприятие отменено"))


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(EventsCog(bot))
