"""Async SQLite database layer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

from .models import GUILD_CONFIG_FIELDS, GuildConfig

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS guilds (
    guild_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    birthday_channel_id INTEGER,
    events_channel_id INTEGER,
    proposals_channel_id INTEGER,
    quotes_channel_id INTEGER,
    statistics_enabled INTEGER NOT NULL DEFAULT 1,
    personal_roles_enabled INTEGER NOT NULL DEFAULT 0,
    auto_execute_proposals INTEGER NOT NULL DEFAULT 0,
    rgb_enabled INTEGER NOT NULL DEFAULT 1,
    birthday_announce_time TEXT NOT NULL DEFAULT '09:00',
    birthday_reminder_days INTEGER NOT NULL DEFAULT 1,
    event_reminder_minutes INTEGER NOT NULL DEFAULT 60,
    rgb_interval_seconds INTEGER NOT NULL DEFAULT 10,
    proposal_duration_hours INTEGER NOT NULL DEFAULT 24,
    proposal_quorum INTEGER NOT NULL DEFAULT 3,
    proposal_pass_ratio REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TEXT,
    display_name_cache TEXT,
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS birthdays (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    day INTEGER NOT NULL CHECK (day BETWEEN 1 AND 31),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    year INTEGER,
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS message_statistics (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (guild_id, user_id, channel_id, date),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_open
    ON voice_sessions(guild_id, user_id, ended_at);

CREATE TABLE IF NOT EXISTS reaction_statistics (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (guild_id, user_id, date),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    starts_at TEXT NOT NULL,
    max_participants INTEGER,
    channel_id INTEGER,
    organizer_id INTEGER NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'cancelled', 'completed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (event_id, user_id),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    added_by INTEGER NOT NULL,
    created_at TEXT,
    saved_at TEXT NOT NULL DEFAULT (datetime('now')),
    reactions_snapshot TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS custom_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    owner_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'managed'
        CHECK (kind IN ('managed', 'personal')),
    rgb_enabled INTEGER NOT NULL DEFAULT 0,
    rgb_speed REAL NOT NULL DEFAULT 1.0,
    rgb_hue REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, role_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    content TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'passed', 'rejected', 'cancelled')),
    ends_at TEXT NOT NULL,
    action_type TEXT,
    action_payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, number),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS proposal_votes (
    proposal_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    vote TEXT NOT NULL CHECK (vote IN ('yes', 'no')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (proposal_id, user_id),
    FOREIGN KEY (proposal_id) REFERENCES proposals(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: str | Path, default_timezone: str = "Europe/Moscow") -> None:
        self.path = Path(path)
        self.default_timezone = default_timezone
        self._db: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._db

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("Database ready at %s", self.path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("Database closed")

    async def ensure_guild(self, guild_id: int) -> GuildConfig:
        existing = await self.get_guild(guild_id)
        if existing is not None:
            return existing

        await self.connection.execute(
            """
            INSERT INTO guilds (guild_id, timezone)
            VALUES (?, ?)
            """,
            (guild_id, self.default_timezone),
        )
        await self.connection.commit()
        config = await self.get_guild(guild_id)
        assert config is not None
        return config

    async def get_guild(self, guild_id: int) -> GuildConfig | None:
        cursor = await self.connection.execute(
            "SELECT * FROM guilds WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return GuildConfig.from_row(row)

    async def update_guild(self, guild_id: int, **fields: Any) -> GuildConfig:
        if not fields:
            config = await self.ensure_guild(guild_id)
            return config

        unknown = set(fields) - GUILD_CONFIG_FIELDS
        if unknown:
            raise ValueError(f"Unknown guild config fields: {sorted(unknown)}")

        await self.ensure_guild(guild_id)

        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values())
        values.append(guild_id)

        await self.connection.execute(
            f"""
            UPDATE guilds
            SET {assignments}, updated_at = datetime('now')
            WHERE guild_id = ?
            """,
            values,
        )
        await self.connection.commit()

        config = await self.get_guild(guild_id)
        assert config is not None
        return config

    async def list_guild_ids(self) -> list[int]:
        cursor = await self.connection.execute("SELECT guild_id FROM guilds")
        rows = await cursor.fetchall()
        return [int(row["guild_id"]) for row in rows]
