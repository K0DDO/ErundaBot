"""Event business logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.database.database import Database
from bot.database.models import Event, GuildConfig
from bot.utils.timezones import format_datetime_local, parse_event_datetime


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
        max_participants: int | None = None,
        channel_id: int | None = None,
    ) -> Event:
        starts_at = parse_event_datetime(date_str, time_str, tz_name)
        if starts_at <= datetime.now(ZoneInfo(tz_name)):
            raise ValueError("Время начала должно быть в будущем")
        if max_participants is not None and max_participants < 1:
            raise ValueError("Лимит участников должен быть ≥ 1")
        event = await self.db.create_event(
            guild_id,
            title.strip(),
            description.strip(),
            starts_at.isoformat(),
            organizer_id,
            max_participants,
            channel_id,
        )
        await self.ensure_organizer_participant(event)
        await self.db.renumber_events(guild_id)
        refreshed = await self.db.get_event(event.id)
        return refreshed if refreshed is not None else event

    async def get(self, event_id: int) -> Event | None:
        return await self.db.get_event(event_id)

    async def get_by_number(self, guild_id: int, number: int) -> Event | None:
        return await self.db.get_event_by_number(guild_id, number)

    async def list_scheduled(self, guild_id: int) -> list[Event]:
        return await self.db.list_events(guild_id, status="scheduled")

    async def list_upcoming(self, guild_id: int) -> list[Event]:
        now = datetime.now(timezone.utc)
        upcoming: list[Event] = []
        for event in await self.db.list_events(guild_id, status="scheduled"):
            starts = datetime.fromisoformat(event.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if starts > now:
                upcoming.append(event)
        return upcoming

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
        if event.organizer_id == user_id:
            raise ValueError("Создатель ивента всегда в списке участников")
        if not await self.db.remove_event_participant(event_id, user_id):
            raise ValueError("Вы не участвуете")
        count = await self.db.count_event_participants(event_id)
        return event, count

    async def participant_count(self, event_id: int) -> int:
        return await self.db.count_event_participants(event_id)

    async def ensure_organizer_participant(self, event: Event) -> None:
        await self.db.add_event_participant(event.id, event.organizer_id)

    async def participants_for_display(self, event: Event) -> list[int]:
        ids = await self.db.list_event_participants(event.id)
        rest = [uid for uid in ids if uid != event.organizer_id]
        if event.organizer_id in ids:
            return [event.organizer_id, *rest]
        return ids

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

    async def due_reminders(self, config: GuildConfig, now: datetime) -> list[Event]:
        if config.events_channel_id is None:
            return []
        tz = ZoneInfo(config.timezone)
        local_now = now.astimezone(tz)
        due: list[Event] = []
        for event in await self.db.list_scheduled_events():
            if event.guild_id != config.guild_id:
                continue
            starts = datetime.fromisoformat(event.starts_at).astimezone(tz)
            if starts <= local_now:
                continue
            delta = starts - local_now
            if delta <= timedelta(minutes=config.event_reminder_minutes):
                if not await self.db.was_event_notified(event.id, "reminder"):
                    due.append(event)
        return due

    async def due_starts(self, config: GuildConfig, now: datetime) -> list[Event]:
        tz = ZoneInfo(config.timezone)
        local_now = now.astimezone(tz)
        due: list[Event] = []
        for event in await self.db.list_scheduled_events():
            if event.guild_id != config.guild_id:
                continue
            starts = datetime.fromisoformat(event.starts_at).astimezone(tz)
            if starts <= local_now and not await self.db.was_event_notified(event.id, "start"):
                due.append(event)
        return due

    async def mark_notified(self, event_id: int, kind: str) -> None:
        await self.db.mark_event_notified(event_id, kind)
