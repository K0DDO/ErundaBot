"""Event business logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from typing import Literal

from bot.database.database import Database
from bot.database.models import Event
from bot.utils.timezones import format_countdown, format_datetime_local, parse_event_datetime

EVENT_AUTO_END_HOURS = 24
PingMode = Literal["countdown", "start", "live"]


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

    async def list_pingable(self, guild_id: int) -> list[Event]:
        return await self.list_scheduled(guild_id)

    async def list_running(self, guild_id: int, now: datetime | None = None) -> list[Event]:
        return [
            event
            for event in await self.list_scheduled(guild_id)
            if self.has_started(event, now)
        ]

    def is_pingable(self, event: Event) -> bool:
        return event.status == "scheduled"

    async def due_auto_end(self, now: datetime) -> list[Event]:
        due: list[Event] = []
        for event in await self.db.list_scheduled_events():
            if not self.has_started(event, now):
                continue
            starts = datetime.fromisoformat(event.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if now >= starts + timedelta(hours=EVENT_AUTO_END_HOURS):
                due.append(event)
        return due

    async def end(self, event_id: int, user_id: int) -> Event:
        event = await self.db.get_event(event_id)
        if event is None:
            raise ValueError("Ивент не найден")
        if event.status != "scheduled":
            raise ValueError("Ивент уже завершён или отменён")
        if not self.has_started(event):
            raise ValueError("Ивент ещё не начался")
        if event.organizer_id != user_id:
            raise ValueError("Завершить может только создатель")
        return event

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

    async def join(self, event_id: int, user_id: int) -> tuple[Event, int]:
        event = await self.db.get_event(event_id)
        if event is None:
            raise ValueError("Ивент не найден")
        if event.status != "scheduled":
            raise ValueError("Регистрация закрыта")
        if await self.db.is_event_participant(event_id, user_id):
            raise ValueError("Вы уже участвуете")
        count = await self.db.count_event_participants(event_id)
        if event.max_participants is not None and count >= event.max_participants:
            raise ValueError("Достигнут лимит участников")
        await self.db.add_event_participant(event_id, user_id)
        return event, count + 1

    async def leave(self, event_id: int, user_id: int) -> tuple[Event, int]:
        event = await self.db.get_event(event_id)
        if event is None:
            raise ValueError("Ивент не найден")
        if not await self.db.remove_event_participant(event_id, user_id):
            raise ValueError("Вы не участвуете")
        count = await self.db.count_event_participants(event_id)
        return event, count

    async def participant_count(self, event_id: int) -> int:
        return await self.db.count_event_participants(event_id)

    async def participants_for_display(self, event: Event) -> list[int]:
        return await self.db.list_event_participants(event.id)

    async def set_message(self, event_id: int, message_id: int) -> Event:
        return await self.db.update_event(event_id, message_id=message_id)

    async def due_starts(self, now: datetime) -> list[Event]:
        due: list[Event] = []
        for event in await self.db.list_scheduled_events():
            starts = datetime.fromisoformat(event.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if now >= starts and not await self.db.was_event_notified(event.id, "start"):
                due.append(event)
        return due

    async def mark_notified(self, event_id: int, kind: str) -> None:
        await self.db.mark_event_notified(event_id, kind)

    def time_display(self, event: Event, tz_name: str, now: datetime | None = None) -> str:
        if event.status == "completed":
            return "Закончился"
        if self.has_started(event, now):
            return "Идёт"
        _, time_label = self.format_starts_at(event, tz_name)
        return time_label

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

    def participant_ping_content(
        self,
        event: Event,
        participant_ids: list[int],
        *,
        mode: PingMode,
    ) -> str | None:
        if not participant_ids:
            return None
        mentions = " ".join(f"<@{uid}>" for uid in participant_ids[:20])
        if mode == "start":
            body = f"Ивент **{event.title}** начинается!"
        elif mode == "live":
            body = f"Ивент **{event.title}** идёт!"
        else:
            left = self.remaining_label(event)
            body = f"До ивента **{event.title}** осталось **{left}**."
        extra = len(participant_ids) - 20
        if extra > 0:
            body += f"\n+{extra}"
        return f"{mentions}\n{body}"

    def manual_ping_mode(self, event: Event, now: datetime | None = None) -> PingMode:
        if self.has_started(event, now):
            return "live"
        return "countdown"
