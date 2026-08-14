"""Centralized background task lifecycle."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from discord.ext import tasks

from bot.utils.embeds import base_embed
from bot.views.proposal_views import build_proposal_embed

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class BackgroundTasks:
    """Starts/stops background loops once per bot lifecycle."""

    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            log.warning("BackgroundTasks.start() called more than once — ignored")
            return
        self._started = True
        if not self.birthday_loop.is_running():
            self.birthday_loop.start()
        if not self.event_loop.is_running():
            self.event_loop.start()
        if not self.proposal_loop.is_running():
            self.proposal_loop.start()
        if not self.birthday_rgb_loop.is_running():
            self.birthday_rgb_loop.start()
        log.info("Background tasks started")

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        for loop in (
            self.birthday_loop,
            self.event_loop,
            self.proposal_loop,
            self.birthday_rgb_loop,
        ):
            if loop.is_running():
                loop.cancel()
        log.info("Background tasks stopped")

    @tasks.loop(minutes=1)
    async def birthday_loop(self) -> None:
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        try:
            guilds = await self.bot.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for birthday loop")
            return

        for config in guilds:
            guild = self.bot.get_guild(config.guild_id)
            if guild is None:
                continue
            try:
                local_now = now.astimezone(ZoneInfo(config.timezone))
                local_today = local_now.date()
                if local_now.hour == 0 and local_now.minute == 0:
                    if config.birthday_channel_id:
                        await self.bot.birthday_service.sync_board(guild, self.bot)
                    await self.bot.birthday_star_service.sync_today(
                        guild, config, local_today, self.bot
                    )

                channel = (
                    guild.get_channel(config.birthday_channel_id)
                    if config.birthday_channel_id
                    else None
                )
                if channel is None or not hasattr(channel, "send"):
                    continue

                announcements = await self.bot.birthday_service.due_announcements(config, now)
                for birthday in announcements:
                    member = guild.get_member(birthday.user_id)
                    if member is not None:
                        try:
                            await self.bot.birthday_star_service.grant(
                                guild, member, granted_on=local_today
                            )
                        except Exception:
                            log.exception("Failed to grant birthday star to %s", birthday.user_id)
                    embed, _used_ai = await self.bot.birthday_service.announce_embed(
                        guild,
                        birthday,
                        local_today,
                        self.bot.ai_service,
                        mention=True,
                    )
                    await channel.send(embed=embed)
                    await self.bot.birthday_service.mark_notified(
                        config.guild_id,
                        birthday.user_id,
                        local_today,
                        "announce",
                    )

                reminders = await self.bot.birthday_service.due_reminders(config, now)
                for birthday, event_date in reminders:
                    embed = self.bot.birthday_service.reminder_embed(
                        guild,
                        birthday,
                        days=config.birthday_reminder_days,
                    )
                    await channel.send(embed=embed)
                    await self.bot.birthday_service.mark_notified(
                        config.guild_id,
                        birthday.user_id,
                        event_date,
                        "reminder",
                    )
            except Exception:
                log.exception("Birthday loop failed for guild %s", config.guild_id)

    @tasks.loop(minutes=1)
    async def event_loop(self) -> None:
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        try:
            guilds = await self.bot.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for event loop")
            return

        for config in guilds:
            guild = self.bot.get_guild(config.guild_id)
            if guild is None:
                continue
            channel_id = config.events_channel_id
            if channel_id is None:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None or not hasattr(channel, "send"):
                continue

            try:
                reminders = await self.bot.event_service.due_reminders(config, now)
                for event in reminders:
                    date_label, time_label = self.bot.event_service.format_starts_at(
                        event, config.timezone
                    )
                    participants = await self.bot.event_service.participants_for_display(event)
                    mentions = " ".join(f"<@{uid}>" for uid in participants[:20])
                    embed = base_embed(
                        title="Напоминание об ивенте",
                        description=(
                            f"**{event.title}** начнётся {date_label} в {time_label}."
                            + (f"\n{mentions}" if mentions else "")
                        ),
                    )
                    await channel.send(embed=embed)
                    await self.bot.event_service.mark_notified(event.id, "reminder")

                starts = await self.bot.event_service.due_starts(config, now)
                for event in starts:
                    participants = await self.bot.event_service.participants_for_display(event)
                    mentions = " ".join(f"<@{uid}>" for uid in participants[:20])
                    embed = base_embed(
                        title="Ивент начинается",
                        description=f"**{event.title}** сейчас!\n{mentions}".strip(),
                    )
                    await channel.send(embed=embed)
                    await self.bot.event_service.mark_notified(event.id, "start")
            except Exception:
                log.exception("Event loop failed for guild %s", config.guild_id)

        try:
            await self.bot.event_service.overdue_to_complete(now)
        except Exception:
            log.exception("Event completion sweep failed")

    @tasks.loop(minutes=1)
    async def proposal_loop(self) -> None:
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        try:
            open_proposals = await self.bot.db.list_open_proposals()
        except Exception:
            log.exception("Failed to load open proposals")
            return

        for proposal in open_proposals:
            ends = datetime.fromisoformat(proposal.ends_at)
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if now < ends.astimezone(timezone.utc):
                continue
            config = await self.bot.db.get_guild(proposal.guild_id)
            if config is None:
                continue
            guild = self.bot.get_guild(proposal.guild_id)
            if guild is None:
                continue
            try:
                updated = await self.bot.democracy_service.close_proposal(
                    proposal, config, self.bot
                )
                if updated.message_id and updated.channel_id:
                    channel = guild.get_channel(updated.channel_id)
                    if channel and hasattr(channel, "fetch_message"):
                        try:
                            msg = await channel.fetch_message(updated.message_id)
                            embed = await build_proposal_embed(
                                self.bot, updated, config.timezone, final=True
                            )
                            await msg.edit(embed=embed, view=None)
                        except Exception:
                            log.exception("Failed to update proposal message %s", updated.id)
            except Exception:
                log.exception("Proposal close failed for %s", proposal.id)

    @birthday_loop.before_loop
    async def before_birthday_loop(self) -> None:
        await self.bot.wait_until_ready()

    @event_loop.before_loop
    async def before_event_loop(self) -> None:
        await self.bot.wait_until_ready()

    @proposal_loop.before_loop
    async def before_proposal_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=8)
    async def birthday_rgb_loop(self) -> None:
        await self.bot.wait_until_ready()
        await self.bot.birthday_star_service.tick_rgb(self.bot)

    @birthday_rgb_loop.before_loop
    async def before_birthday_rgb_loop(self) -> None:
        await self.bot.wait_until_ready()
