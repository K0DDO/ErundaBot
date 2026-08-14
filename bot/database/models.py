"""Dataclasses and constants for DB rows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    timezone: str = "Europe/Moscow"
    birthday_channel_id: int | None = None
    events_channel_id: int | None = None
    proposals_channel_id: int | None = None
    quotes_channel_id: int | None = None
    fest_channel_id: int | None = None
    tgk_channel_id: int | None = None
    statistics_enabled: bool = True
    personal_roles_enabled: bool = True
    auto_execute_proposals: bool = False
    rgb_enabled: bool = True
    birthday_announce_time: str = "09:00"
    birthday_reminder_days: int = 1
    event_reminder_minutes: int = 60
    rgb_interval_seconds: int = 10
    proposal_duration_hours: int = 24
    proposal_quorum: int = 3
    proposal_pass_ratio: float = 0.5
    birthday_board_message_id: int | None = None
    birthday_star_role_id: int | None = None
    fest_staff_role_id: int | None = None
    fest_ping_role_id: int | None = None
    fest_reminder_minutes: int = 60
    tgk_board_message_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> GuildConfig:
        return cls(
            guild_id=row["guild_id"],
            timezone=row["timezone"],
            birthday_channel_id=row["birthday_channel_id"],
            events_channel_id=row["events_channel_id"],
            proposals_channel_id=row["proposals_channel_id"],
            quotes_channel_id=row["quotes_channel_id"],
            fest_channel_id=row["fest_channel_id"] if "fest_channel_id" in row.keys() else None,
            tgk_channel_id=row["tgk_channel_id"] if "tgk_channel_id" in row.keys() else None,
            statistics_enabled=bool(row["statistics_enabled"]),
            personal_roles_enabled=bool(row["personal_roles_enabled"]),
            auto_execute_proposals=bool(row["auto_execute_proposals"]),
            rgb_enabled=bool(row["rgb_enabled"]),
            birthday_announce_time=row["birthday_announce_time"],
            birthday_reminder_days=row["birthday_reminder_days"],
            event_reminder_minutes=row["event_reminder_minutes"],
            rgb_interval_seconds=row["rgb_interval_seconds"],
            proposal_duration_hours=row["proposal_duration_hours"],
            proposal_quorum=row["proposal_quorum"],
            proposal_pass_ratio=float(row["proposal_pass_ratio"]),
            birthday_board_message_id=row["birthday_board_message_id"]
            if "birthday_board_message_id" in row.keys()
            else None,
            birthday_star_role_id=row["birthday_star_role_id"]
            if "birthday_star_role_id" in row.keys()
            else None,
            fest_staff_role_id=row["fest_staff_role_id"]
            if "fest_staff_role_id" in row.keys()
            else None,
            fest_ping_role_id=row["fest_ping_role_id"]
            if "fest_ping_role_id" in row.keys()
            else None,
            fest_reminder_minutes=int(row["fest_reminder_minutes"])
            if "fest_reminder_minutes" in row.keys() and row["fest_reminder_minutes"] is not None
            else 60,
            tgk_board_message_id=row["tgk_board_message_id"]
            if "tgk_board_message_id" in row.keys()
            else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class Birthday:
    guild_id: int
    user_id: int
    day: int
    month: int
    year: int | None = None
    emoji: str = "👤"

    @classmethod
    def from_row(cls, row: Any) -> Birthday:
        keys = row.keys()
        return cls(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            day=row["day"],
            month=row["month"],
            year=row["year"],
            emoji=row["emoji"] if "emoji" in keys and row["emoji"] else "👤",
        )


@dataclass(slots=True)
class Event:
    id: int
    guild_id: int
    title: str
    description: str
    starts_at: str
    max_participants: int | None
    channel_id: int | None
    organizer_id: int
    message_id: int | None
    status: str
    number: int = 0

    @classmethod
    def from_row(cls, row: Any) -> Event:
        keys = row.keys()
        number = row["number"] if "number" in keys else None
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            title=row["title"],
            description=row["description"],
            starts_at=row["starts_at"],
            max_participants=row["max_participants"],
            channel_id=row["channel_id"],
            organizer_id=row["organizer_id"],
            message_id=row["message_id"],
            status=row["status"],
            number=int(number) if number else 0,
        )


@dataclass(slots=True)
class Quote:
    id: int
    guild_id: int
    content: str
    author_id: int
    channel_id: int | None
    message_id: int | None
    added_by: int
    created_at: str | None
    saved_at: str
    reactions_snapshot: str
    author_display: str | None = None
    posted_channel_id: int | None = None
    posted_message_id: int | None = None
    number: int = 0
    author_ids: list[int] = field(default_factory=list)

    def linked_author_ids(self) -> list[int]:
        ids: list[int] = []
        for value in self.author_ids:
            if value and value not in ids:
                ids.append(int(value))
        if self.author_id and self.author_id not in ids:
            ids.insert(0, int(self.author_id))
        return ids

    @classmethod
    def from_row(cls, row: Any) -> Quote:
        keys = row.keys()
        number = row["number"] if "number" in keys else None
        raw_ids = row["author_ids"] if "author_ids" in keys else "[]"
        try:
            parsed = json.loads(raw_ids or "[]")
        except (TypeError, json.JSONDecodeError):
            parsed = []
        author_ids = []
        for value in parsed:
            try:
                author_id = int(value)
            except (TypeError, ValueError):
                continue
            if author_id and author_id not in author_ids:
                author_ids.append(author_id)
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            content=row["content"],
            author_id=row["author_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            added_by=row["added_by"],
            created_at=row["created_at"],
            saved_at=row["saved_at"],
            reactions_snapshot=row["reactions_snapshot"],
            author_display=row["author_display"] if "author_display" in keys else None,
            posted_channel_id=row["posted_channel_id"] if "posted_channel_id" in keys else None,
            posted_message_id=row["posted_message_id"] if "posted_message_id" in keys else None,
            number=int(number) if number else row["id"],
            author_ids=author_ids,
        )


@dataclass(slots=True)
class BirthdayStarGrant:
    guild_id: int
    user_id: int
    hidden_role_ids: str = "[]"
    granted_on: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> BirthdayStarGrant:
        return cls(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            hidden_role_ids=row["hidden_role_ids"] or "[]",
            granted_on=row["granted_on"],
        )


@dataclass(slots=True)
class CustomRole:
    id: int
    guild_id: int
    role_id: int
    owner_id: int | None
    kind: str
    rgb_enabled: bool
    rgb_speed: float
    rgb_hue: float

    @classmethod
    def from_row(cls, row: Any) -> CustomRole:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            role_id=row["role_id"],
            owner_id=row["owner_id"],
            kind=row["kind"],
            rgb_enabled=bool(row["rgb_enabled"]),
            rgb_speed=float(row["rgb_speed"]),
            rgb_hue=float(row["rgb_hue"]),
        )


@dataclass(slots=True)
class Proposal:
    id: int
    guild_id: int
    number: int
    content: str
    author_id: int
    channel_id: int | None
    message_id: int | None
    status: str
    ends_at: str
    action_type: str | None
    action_payload: str | None

    @classmethod
    def from_row(cls, row: Any) -> Proposal:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            number=row["number"],
            content=row["content"],
            author_id=row["author_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            status=row["status"],
            ends_at=row["ends_at"],
            action_type=row["action_type"],
            action_payload=row["action_payload"],
        )


@dataclass(slots=True)
class Festival:
    id: int
    guild_id: int
    number: int
    starts_at: str
    channel_id: int | None
    message_id: int | None
    winner_user_id: int | None
    winner_film: str | None
    status: str
    reminder_sent: bool = False

    @classmethod
    def from_row(cls, row: Any) -> Festival:
        keys = row.keys()
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            number=row["number"],
            starts_at=row["starts_at"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            winner_user_id=row["winner_user_id"],
            winner_film=row["winner_film"],
            status=row["status"],
            reminder_sent=bool(row["reminder_sent"]) if "reminder_sent" in keys else False,
        )


@dataclass(slots=True)
class FestivalFilm:
    festival_id: int
    user_id: int
    title: str
    image_url: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> FestivalFilm:
        keys = row.keys()
        return cls(
            festival_id=row["festival_id"],
            user_id=row["user_id"],
            title=row["title"],
            image_url=row["image_url"] if "image_url" in keys else None,
        )


@dataclass(slots=True)
class TgChannel:
    id: int
    guild_id: int
    user_id: int
    number: int
    title: str
    url: str
    image_url: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> TgChannel:
        keys = row.keys()
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            number=row["number"],
            title=row["title"],
            url=row["url"],
            image_url=row["image_url"] if "image_url" in keys else None,
        )


# Columns that /config may update (whitelist).
GUILD_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "timezone",
        "birthday_channel_id",
        "events_channel_id",
        "proposals_channel_id",
        "quotes_channel_id",
        "fest_channel_id",
        "tgk_channel_id",
        "statistics_enabled",
        "personal_roles_enabled",
        "auto_execute_proposals",
        "rgb_enabled",
        "birthday_announce_time",
        "birthday_reminder_days",
        "event_reminder_minutes",
        "rgb_interval_seconds",
        "proposal_duration_hours",
        "proposal_quorum",
        "proposal_pass_ratio",
        "birthday_star_role_id",
        "fest_staff_role_id",
        "fest_ping_role_id",
        "fest_reminder_minutes",
        "tgk_board_message_id",
    }
)
