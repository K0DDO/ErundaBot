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
            created_at=row["created_at"],
            updated_at=row["updated_at"],
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
