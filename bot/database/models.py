"""Dataclasses and constants for DB rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    timezone: str = "Europe/Moscow"
    birthday_channel_id: int | None = None
    events_channel_id: int | None = None
    proposals_channel_id: int | None = None
    quotes_channel_id: int | None = None
    statistics_enabled: bool = True
    personal_roles_enabled: bool = False
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

    @classmethod
    def from_row(cls, row: Any) -> Birthday:
        return cls(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            day=row["day"],
            month=row["month"],
            year=row["year"],
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

    @classmethod
    def from_row(cls, row: Any) -> Event:
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

    @classmethod
    def from_row(cls, row: Any) -> Quote:
        keys = row.keys()
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


# Columns that /config may update (whitelist).
GUILD_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "timezone",
        "birthday_channel_id",
        "events_channel_id",
        "proposals_channel_id",
        "quotes_channel_id",
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
    }
)
