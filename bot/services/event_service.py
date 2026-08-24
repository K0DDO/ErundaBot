"""Event business logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord

from bot.database.database import Database
from bot.database.models import Event
from bot.utils.timezones import format_countdown, format_datetime_local, parse_event_datetime


class EventService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        guild_id: int,
        title: str,
        description: str,
        date_str: str,
        time_str: str,
        organizer_id: int,
        tz_name: str,
        ping_role_id: int,
        channel_id: int | None = None,
    ) -> Event:
        starts_at = parse_event_datetime(date_str, time_str, tz_name)
        if starts_at <= datetime.now(ZoneInfo(tz_name)):
            raise ValueError("Время начала должно быть в будущем")
        event = await self.db.create_event(
            guild_id,
            title.strip(),
            description.strip(),
            starts_at.isoformat(),
            organizer_id,
            ping_role_id,
            channel_id,
        )
        await self.db.renumber_events(guild_id)
        refreshed = await self.db.get_event(event.id)
        return refreshed if refreshed is not None else event

    async def get(self, event_id: int) -> Event | None:
        return await self.db.get_event(event_id)

    async def get_by_number(self, guild_id: int, number: int) -> Event | None:
        return await self.db.get_event_by_number(guild_id, number)

    async def list_scheduled(self, guild_id: int) -> list[Event]:
        return await self.db.list_events(guild_id, status="scheduled")

    async def cancel(self, event_id: int, user_id: int) -> Event:
        event = await self.db.get_event(event_id)
        if event is None:
            raise ValueError("Ивент не найден")
        if event.status != "scheduled":
            raise ValueError("Ивент уже завершён или отменён")
        if event.organizer_id != user_id:
            raise ValueError("Отменить может только создатель")
        return event

    async def delete_and_renumber(self, event: Event) -> list[Event]:
        guild_id = event.guild_id
        await self.db.delete_event(event.id)
        return await self.db.renumber_events(guild_id)

    async def set_message(self, event_id: int, message_id: int) -> Event:
        return await self.db.update_event(event_id, message_id=message_id)

    async def overdue_events(self, now: datetime) -> list[Event]:
        due: list[Event] = []
        for event in await self.db.list_scheduled_events():
            starts = datetime.fromisoformat(event.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if now >= starts + timedelta(hours=2):
                due.append(event)
        return due

    def format_starts_at(self, event: Event, tz_name: str) -> tuple[str, str]:
        dt = datetime.fromisoformat(event.starts_at)
        return format_datetime_local(dt, tz_name)

    def has_started(self, event: Event, now: datetime | None = None) -> bool:
        starts = datetime.fromisoformat(event.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return current >= starts

    def remaining_label(self, event: Event, now: datetime | None = None) -> str:
        starts = datetime.fromisoformat(event.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return format_countdown((starts - current).total_seconds())

    def ping_text(self, event: Event, role: discord.Role) -> str:
        if self.has_started(event):
            return f"{role.mention} ивент **{event.title}** идёт!"
        left = self.remaining_label(event)
        return f"{role.mention} до ивента **{event.title}** осталось **{left}**."
