"""Async Postgres database layer (asyncpg)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from bot.database.pg_compat import CompatConnection
from bot.settings import is_home_guild
from bot.utils.birthday_emojis import pick_birthday_emoji

FESTIVAL_START_NUMBER = 29

from .models import (
    GUILD_CONFIG_FIELDS,
    Birthday,
    BirthdayStarGrant,
    CustomRole,
    Event,
    Festival,
    FestivalFilm,
    FestivalRatingLog,
    FestivalVoiceStats,
    GuildConfig,
    Quote,
    SantaAssignment,
    SantaEntry,
    SantaEvent,
    TgChannel,
    DailyTextLog,
    VoiceDossierEntry,
    VoiceSnippet,
)

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id BIGINT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    birthday_channel_id BIGINT,
    events_channel_id BIGINT,
    proposals_channel_id BIGINT,
    quotes_channel_id BIGINT,
    fest_channel_id BIGINT,
    tgk_channel_id BIGINT,
    santa_channel_id BIGINT,
    statistics_enabled INTEGER NOT NULL DEFAULT 1,
    personal_roles_enabled INTEGER NOT NULL DEFAULT 1,
    auto_execute_proposals INTEGER NOT NULL DEFAULT 0,
    rgb_enabled INTEGER NOT NULL DEFAULT 1,
    birthday_announce_time TEXT NOT NULL DEFAULT '09:00',
    birthday_reminder_days INTEGER NOT NULL DEFAULT 1,
    event_reminder_minutes INTEGER NOT NULL DEFAULT 60,
    rgb_interval_seconds INTEGER NOT NULL DEFAULT 10,
    proposal_duration_hours INTEGER NOT NULL DEFAULT 24,
    proposal_quorum INTEGER NOT NULL DEFAULT 3,
    proposal_pass_ratio REAL NOT NULL DEFAULT 0.5,
    birthday_board_message_id BIGINT,
    birthday_star_role_id BIGINT,
    fest_staff_role_id BIGINT,
    fest_ping_role_id BIGINT,
    config_role_id BIGINT,
    fest_reminder_minutes INTEGER NOT NULL DEFAULT 60,
    fest_presence_check INTEGER NOT NULL DEFAULT 1,
    tarot_reset_minutes INTEGER NOT NULL DEFAULT 10,
    tarot_show_meaning INTEGER NOT NULL DEFAULT 1,
    voice_chat_transcripts INTEGER NOT NULL DEFAULT 1,
    voice_listen_channel_id BIGINT,
    voice_announce_channel_id BIGINT,
    tgk_board_message_id BIGINT,
    tgk_board_message_ids TEXT NOT NULL DEFAULT '[]',
    tgk_list_role_id BIGINT,
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
    emoji TEXT NOT NULL DEFAULT '👤',
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
    number INTEGER NOT NULL DEFAULT 0,
    ping_role_id INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'cancelled', 'completed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, number),
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
    author_display TEXT,
    posted_channel_id INTEGER,
    posted_message_id INTEGER,
    number INTEGER NOT NULL DEFAULT 0,
    author_ids TEXT NOT NULL DEFAULT '[]',
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

CREATE TABLE IF NOT EXISTS birthday_star_grants (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    hidden_role_ids TEXT NOT NULL DEFAULT '[]',
    granted_on TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS birthday_notifications (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('announce', 'reminder')),
    channel_id INTEGER,
    message_id INTEGER,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id, event_date, kind),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_notifications (
    event_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('reminder', 'start')),
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (event_id, kind),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS festivals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    starts_at TEXT NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    winner_user_id INTEGER,
    winner_film TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed')),
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, number),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS festival_films (
    festival_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    image_url TEXT,
    age_rating TEXT,
    runtime_minutes INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (festival_id, user_id),
    FOREIGN KEY (festival_id) REFERENCES festivals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS festival_ratings (
    festival_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 10),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (festival_id, user_id),
    FOREIGN KEY (festival_id) REFERENCES festivals(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS festival_rating_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    festival_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 10),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (festival_id) REFERENCES festivals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_festival_rating_logs_fest
ON festival_rating_logs(festival_id, created_at, id);

CREATE TABLE IF NOT EXISTS festival_voice_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    festival_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY (festival_id) REFERENCES festivals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_festival_voice_open
ON festival_voice_segments(festival_id, user_id, ended_at);

CREATE TABLE IF NOT EXISTS tarot_draws (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    card_id TEXT NOT NULL,
    reversed INTEGER NOT NULL DEFAULT 0,
    drawn_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id, card_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS festival_blocked_films (
    guild_id INTEGER NOT NULL,
    title_key TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, title_key),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tg_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    image_url TEXT,
    description TEXT,
    telegram_chat_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, number),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS santa_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    drawn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
    UNIQUE (guild_id, number)
);

CREATE TABLE IF NOT EXISTS santa_entries (
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    address TEXT NOT NULL,
    wishlist_url TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (event_id, user_id),
    FOREIGN KEY (event_id) REFERENCES santa_events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS santa_assignments (
    event_id INTEGER NOT NULL,
    giver_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, giver_id),
    FOREIGN KEY (event_id) REFERENCES santa_events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_snippets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_snippets_pending
ON voice_snippets(guild_id, user_id, processed);

CREATE TABLE IF NOT EXISTS voice_dossier (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    fact TEXT NOT NULL,
    source_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_dossier_user
ON voice_dossier(guild_id, user_id, created_at);

CREATE TABLE IF NOT EXISTS voice_swear_stats (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS daily_text_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL CHECK (source IN ('chat', 'voice')),
    text TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_daily_text_logs_guild_day_user
ON daily_text_logs(guild_id, day, user_id);

CREATE TABLE IF NOT EXISTS member_profiles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    gender TEXT NOT NULL DEFAULT 'u'
        CHECK (gender IN ('f', 'm', 'u')),
    score_f INTEGER NOT NULL DEFAULT 0,
    score_m INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_listen_muted (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id),
    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    def __init__(
        self,
        dsn: str | None = None,
        *,
        path: str | Path | None = None,
        default_timezone: str = "Europe/Moscow",
    ) -> None:
        # path kept for migrate script / temporary local fallback messaging.
        self.path = Path(path) if path else Path("./data/erunda.db")
        self.dsn = (dsn or os.getenv("DATABASE_URL") or "").strip()
        self.default_timezone = default_timezone
        self._db: CompatConnection | None = None
        self._pool: asyncpg.Pool | None = None

    @property
    def connection(self) -> CompatConnection:
        if self._db is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._db

    async def connect(self) -> None:
        if not self.dsn:
            raise RuntimeError(
                "DATABASE_URL is not set (postgresql://user:pass@host:5432/db)"
            )
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        self._db = CompatConnection(self._pool)
        await self._db.executescript(SCHEMA)
        await self._migrate()
        log.info("Database ready (Postgres)")

    async def _migrate(self) -> None:
        """Additive Postgres migrations via schema_migrations."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
        )
        row = await cursor.fetchone()
        version = int(row["v"]) if row else 0
        if version < 33:
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_listen_muted (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id),
                    FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
                )
                """
            )
            await self._db.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (33,),
            )
            version = 33
        log.debug("schema version=%s", version)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            self._pool = None
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

    async def set_birthday_board_message_id(
        self, guild_id: int, message_id: int | None
    ) -> None:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            "UPDATE guilds SET birthday_board_message_id = ?, updated_at = datetime('now') WHERE guild_id = ?",
            (message_id, guild_id),
        )
        await self.connection.commit()

    async def list_guild_ids(self) -> list[int]:
        cursor = await self.connection.execute("SELECT guild_id FROM guilds")
        rows = await cursor.fetchall()
        return [int(row["guild_id"]) for row in rows]

    async def list_guilds(self) -> list[GuildConfig]:
        cursor = await self.connection.execute("SELECT * FROM guilds")
        rows = await cursor.fetchall()
        return [GuildConfig.from_row(row) for row in rows]

    async def upsert_birthday(
        self,
        guild_id: int,
        user_id: int,
        day: int,
        month: int,
        year: int | None,
        emoji: str | None = None,
    ) -> Birthday:
        await self.ensure_guild(guild_id)
        existing = await self.get_birthday(guild_id, user_id)
        resolved_emoji = emoji if emoji is not None else (
            existing.emoji if existing is not None else pick_birthday_emoji(user_id)
        )
        await self.connection.execute(
            """
            INSERT INTO birthdays (guild_id, user_id, day, month, year, emoji)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                day = excluded.day,
                month = excluded.month,
                year = excluded.year,
                emoji = excluded.emoji
            """,
            (guild_id, user_id, day, month, year, resolved_emoji),
        )
        await self.connection.commit()
        birthday = await self.get_birthday(guild_id, user_id)
        assert birthday is not None
        return birthday

    async def get_birthday(self, guild_id: int, user_id: int) -> Birthday | None:
        cursor = await self.connection.execute(
            "SELECT * FROM birthdays WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Birthday.from_row(row)

    async def remove_birthday(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM birthdays WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def update_birthday_emoji(self, guild_id: int, user_id: int, emoji: str) -> None:
        await self.connection.execute(
            "UPDATE birthdays SET emoji = ? WHERE guild_id = ? AND user_id = ?",
            (emoji, guild_id, user_id),
        )
        await self.connection.commit()

    async def list_birthdays(self, guild_id: int) -> list[Birthday]:
        cursor = await self.connection.execute(
            "SELECT * FROM birthdays WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [Birthday.from_row(row) for row in rows]

    async def was_birthday_notified(
        self,
        guild_id: int,
        user_id: int,
        event_date: str,
        kind: str,
    ) -> bool:
        cursor = await self.connection.execute(
            """
            SELECT 1 FROM birthday_notifications
            WHERE guild_id = ? AND user_id = ? AND event_date = ? AND kind = ?
            """,
            (guild_id, user_id, event_date, kind),
        )
        return await cursor.fetchone() is not None

    async def mark_birthday_notified(
        self,
        guild_id: int,
        user_id: int,
        event_date: str,
        kind: str,
        *,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        await self.connection.execute(
            """
            INSERT INTO birthday_notifications
                (guild_id, user_id, event_date, kind, channel_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, event_date, kind) DO UPDATE SET
                channel_id = COALESCE(excluded.channel_id, birthday_notifications.channel_id),
                message_id = COALESCE(excluded.message_id, birthday_notifications.message_id),
                sent_at = birthday_notifications.sent_at
            """,
            (guild_id, user_id, event_date, kind, channel_id, message_id),
        )
        await self.connection.commit()

    async def list_birthday_notification_messages(
        self,
        guild_id: int,
        *,
        before_date: str | None = None,
        user_id: int | None = None,
        event_date: str | None = None,
    ) -> list[tuple[int, int, int, str, str]]:
        """Return (user_id, channel_id, message_id, event_date, kind) with saved messages."""
        clauses = [
            "guild_id = ?",
            "channel_id IS NOT NULL",
            "message_id IS NOT NULL",
        ]
        params: list[object] = [guild_id]
        if before_date is not None:
            clauses.append("event_date < ?")
            params.append(before_date)
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if event_date is not None:
            clauses.append("event_date = ?")
            params.append(event_date)
        where = " AND ".join(clauses)
        cursor = await self.connection.execute(
            f"""
            SELECT user_id, channel_id, message_id, event_date, kind
            FROM birthday_notifications
            WHERE {where}
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [
            (
                int(row["user_id"]),
                int(row["channel_id"]),
                int(row["message_id"]),
                str(row["event_date"]),
                str(row["kind"]),
            )
            for row in rows
        ]

    async def clear_birthday_notification_message(
        self,
        guild_id: int,
        user_id: int,
        event_date: str,
        kind: str,
    ) -> None:
        await self.connection.execute(
            """
            UPDATE birthday_notifications
            SET channel_id = NULL, message_id = NULL
            WHERE guild_id = ? AND user_id = ? AND event_date = ? AND kind = ?
            """,
            (guild_id, user_id, event_date, kind),
        )
        await self.connection.commit()

    async def increment_messages(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        day: str,
        amount: int = 1,
    ) -> None:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            """
            INSERT INTO message_statistics (guild_id, user_id, channel_id, date, count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, channel_id, date) DO UPDATE SET
                count = message_statistics.count + excluded.count
            """,
            (guild_id, user_id, channel_id, day, amount),
        )
        await self.connection.commit()

    async def increment_reactions(
        self,
        guild_id: int,
        user_id: int,
        day: str,
        amount: int = 1,
    ) -> None:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            """
            INSERT INTO reaction_statistics (guild_id, user_id, date, count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, date) DO UPDATE SET
                count = GREATEST(0, reaction_statistics.count + excluded.count)
            """,
            (guild_id, user_id, day, amount),
        )
        await self.connection.commit()

    async def open_voice_session(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        started_at: str,
    ) -> None:
        await self.ensure_guild(guild_id)
        # Avoid duplicate open sessions for same user.
        await self.connection.execute(
            """
            UPDATE voice_sessions
            SET ended_at = ?, duration_seconds = GREATEST(
                0,
                CAST(
                    EXTRACT(
                        EPOCH FROM ((?::text)::timestamptz - started_at::timestamptz)
                    ) AS INTEGER
                )
            )
            WHERE guild_id = ? AND user_id = ? AND ended_at IS NULL
            """,
            (started_at, started_at, guild_id, user_id),
        )
        await self.connection.execute(
            """
            INSERT INTO voice_sessions (guild_id, user_id, channel_id, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, channel_id, started_at),
        )
        await self.connection.commit()

    async def close_voice_session(
        self,
        guild_id: int,
        user_id: int,
        ended_at: str,
    ) -> None:
        await self.connection.execute(
            """
            UPDATE voice_sessions
            SET ended_at = ?,
                duration_seconds = GREATEST(
                    0,
                    CAST(
                        EXTRACT(
                            EPOCH FROM ((?::text)::timestamptz - started_at::timestamptz)
                        ) AS INTEGER
                    )
                )
            WHERE guild_id = ? AND user_id = ? AND ended_at IS NULL
            """,
            (ended_at, ended_at, guild_id, user_id),
        )
        await self.connection.commit()

    async def close_all_open_voice_sessions(self, ended_at: str) -> int:
        cursor = await self.connection.execute(
            """
            UPDATE voice_sessions
            SET ended_at = ?,
                duration_seconds = GREATEST(
                    0,
                    CAST(
                        EXTRACT(
                            EPOCH FROM ((?::text)::timestamptz - started_at::timestamptz)
                        ) AS INTEGER
                    )
                )
            WHERE ended_at IS NULL
            """,
            (ended_at, ended_at),
        )
        await self.connection.commit()
        return cursor.rowcount

    async def sum_messages(
        self,
        guild_id: int,
        user_id: int | None,
        date_from: str | None,
        date_to: str | None,
    ) -> int | list[tuple[int, int]]:
        """If user_id set — return int total. Else return list[(user_id, count)]."""
        clauses = ["guild_id = ?"]
        params: list[Any] = [guild_id]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if date_from is not None:
            clauses.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("date <= ?")
            params.append(date_to)
        where = " AND ".join(clauses)
        if user_id is not None:
            cursor = await self.connection.execute(
                f"SELECT COALESCE(SUM(count), 0) AS total FROM message_statistics WHERE {where}",
                params,
            )
            row = await cursor.fetchone()
            return int(row["total"])
        cursor = await self.connection.execute(
            f"""
            SELECT user_id, SUM(count) AS total
            FROM message_statistics
            WHERE {where}
            GROUP BY user_id
            ORDER BY total DESC
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [(int(r["user_id"]), int(r["total"])) for r in rows]

    async def sum_reactions(
        self,
        guild_id: int,
        user_id: int | None,
        date_from: str | None,
        date_to: str | None,
    ) -> int | list[tuple[int, int]]:
        clauses = ["guild_id = ?"]
        params: list[Any] = [guild_id]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if date_from is not None:
            clauses.append("date >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("date <= ?")
            params.append(date_to)
        where = " AND ".join(clauses)
        if user_id is not None:
            cursor = await self.connection.execute(
                f"SELECT COALESCE(SUM(count), 0) AS total FROM reaction_statistics WHERE {where}",
                params,
            )
            row = await cursor.fetchone()
            return int(row["total"])
        cursor = await self.connection.execute(
            f"""
            SELECT user_id, SUM(count) AS total
            FROM reaction_statistics
            WHERE {where}
            GROUP BY user_id
            ORDER BY total DESC
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [(int(r["user_id"]), int(r["total"])) for r in rows]

    async def sum_voice_seconds(
        self,
        guild_id: int,
        user_id: int | None,
        range_start_iso: str | None,
        range_end_iso: str | None,
        now_iso: str,
    ) -> int | list[tuple[int, int]]:
        """Sum voice seconds overlapping [range_start, range_end], open sessions use now_iso."""
        # Overlap: started_at < end AND (ended_at IS NULL OR ended_at > start)
        # Count clipped duration in Python for correctness.
        clauses = ["guild_id = ?"]
        params: list[Any] = [guild_id]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if range_start_iso is not None:
            clauses.append("(ended_at IS NULL OR ended_at > ?)")
            params.append(range_start_iso)
        if range_end_iso is not None:
            clauses.append("started_at < ?")
            params.append(range_end_iso)
        where = " AND ".join(clauses)
        cursor = await self.connection.execute(
            f"""
            SELECT user_id, started_at, ended_at
            FROM voice_sessions
            WHERE {where}
            """,
            params,
        )
        rows = await cursor.fetchall()
        from datetime import datetime

        def parse(ts: str) -> datetime:
            return datetime.fromisoformat(ts)

        start_bound = parse(range_start_iso) if range_start_iso else None
        end_bound = parse(range_end_iso) if range_end_iso else parse(now_iso)
        now_dt = parse(now_iso)

        totals: dict[int, int] = {}
        for row in rows:
            uid = int(row["user_id"])
            started = parse(row["started_at"])
            ended = parse(row["ended_at"]) if row["ended_at"] else now_dt
            lo = max(started, start_bound) if start_bound else started
            hi = min(ended, end_bound)
            seconds = max(0, int((hi - lo).total_seconds()))
            totals[uid] = totals.get(uid, 0) + seconds

        if user_id is not None:
            return totals.get(user_id, 0)
        return sorted(totals.items(), key=lambda item: item[1], reverse=True)

    # --- Events ---

    async def create_event(
        self,
        guild_id: int,
        title: str,
        description: str,
        starts_at: str,
        organizer_id: int,
        ping_role_id: int,
        channel_id: int | None,
    ) -> Event:
        await self.ensure_guild(guild_id)
        number = await self.next_event_number(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO events (
                guild_id, title, description, starts_at,
                channel_id, organizer_id, number, ping_role_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                title,
                description,
                starts_at,
                channel_id,
                organizer_id,
                number,
                ping_role_id,
            ),
        )
        await self.connection.commit()
        event = await self.get_event(cursor.lastrowid)
        assert event is not None
        return event

    async def get_event(self, event_id: int) -> Event | None:
        cursor = await self.connection.execute(
            "SELECT * FROM events WHERE id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        return Event.from_row(row) if row else None

    async def get_event_by_message(self, message_id: int) -> Event | None:
        cursor = await self.connection.execute(
            "SELECT * FROM events WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return Event.from_row(row) if row else None

    async def update_event(self, event_id: int, **fields: Any) -> Event:
        allowed = {
            "title",
            "description",
            "starts_at",
            "max_participants",
            "channel_id",
            "message_id",
            "status",
            "number",
            "ping_role_id",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown event fields: {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [event_id]
        await self.connection.execute(
            f"UPDATE events SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            values,
        )
        await self.connection.commit()
        event = await self.get_event(event_id)
        assert event is not None
        return event

    async def list_events(
        self,
        guild_id: int,
        status: str | None = None,
    ) -> list[Event]:
        if status:
            cursor = await self.connection.execute(
                "SELECT * FROM events WHERE guild_id = ? AND status = ? ORDER BY starts_at, id",
                (guild_id, status),
            )
        else:
            cursor = await self.connection.execute(
                "SELECT * FROM events WHERE guild_id = ? ORDER BY starts_at DESC",
                (guild_id,),
            )
        rows = await cursor.fetchall()
        return [Event.from_row(r) for r in rows]

    async def list_scheduled_events(self) -> list[Event]:
        cursor = await self.connection.execute(
            "SELECT * FROM events WHERE status = 'scheduled' ORDER BY starts_at",
        )
        rows = await cursor.fetchall()
        return [Event.from_row(r) for r in rows]

    async def next_event_number(self, guild_id: int) -> int:
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM events "
            "WHERE guild_id = ? AND status = 'scheduled'",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 1

    async def get_event_by_number(self, guild_id: int, number: int) -> Event | None:
        cursor = await self.connection.execute(
            "SELECT * FROM events WHERE guild_id = ? AND number = ? AND status = 'scheduled'",
            (guild_id, number),
        )
        row = await cursor.fetchone()
        return Event.from_row(row) if row else None

    async def delete_event(self, event_id: int) -> None:
        await self.connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
        await self.connection.commit()

    async def renumber_events(self, guild_id: int) -> list[Event]:
        events = await self.list_events(guild_id, status="scheduled")
        if not events:
            return []
        for index, event in enumerate(events, start=1):
            await self.connection.execute(
                "UPDATE events SET number = ? WHERE id = ?",
                (-index, event.id),
            )
        for index, event in enumerate(events, start=1):
            await self.connection.execute(
                "UPDATE events SET number = ? WHERE id = ?",
                (index, event.id),
            )
            event.number = index
        await self.connection.commit()
        return events

    async def add_event_participant(self, event_id: int, user_id: int) -> None:
        await self.connection.execute(
            "INSERT OR IGNORE INTO event_participants (event_id, user_id) VALUES (?, ?)",
            (event_id, user_id),
        )
        await self.connection.commit()

    async def remove_event_participant(self, event_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def is_event_participant(self, event_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        return await cursor.fetchone() is not None

    async def count_event_participants(self, event_id: int) -> int:
        cursor = await self.connection.execute(
            "SELECT COUNT(*) AS c FROM event_participants WHERE event_id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        return int(row["c"])

    async def list_event_participants(self, event_id: int) -> list[int]:
        cursor = await self.connection.execute(
            "SELECT user_id FROM event_participants WHERE event_id = ? ORDER BY joined_at",
            (event_id,),
        )
        rows = await cursor.fetchall()
        return [int(r["user_id"]) for r in rows]

    async def was_event_notified(self, event_id: int, kind: str) -> bool:
        cursor = await self.connection.execute(
            "SELECT 1 FROM event_notifications WHERE event_id = ? AND kind = ?",
            (event_id, kind),
        )
        return await cursor.fetchone() is not None

    async def mark_event_notified(self, event_id: int, kind: str) -> None:
        await self.connection.execute(
            "INSERT OR IGNORE INTO event_notifications (event_id, kind) VALUES (?, ?)",
            (event_id, kind),
        )
        await self.connection.commit()

    # --- Quotes ---

    async def next_quote_number(self, guild_id: int) -> int:
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM quotes WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 1

    async def add_quote(
        self,
        guild_id: int,
        content: str,
        author_id: int,
        added_by: int,
        channel_id: int | None,
        message_id: int | None,
        created_at: str | None,
        reactions_snapshot: str,
        author_display: str | None = None,
        author_ids: list[int] | None = None,
    ) -> Quote:
        await self.ensure_guild(guild_id)
        number = await self.next_quote_number(guild_id)
        linked = list(author_ids or [])
        if author_id and author_id not in linked:
            linked.insert(0, author_id)
        primary = linked[0] if linked else author_id
        cursor = await self.connection.execute(
            """
            INSERT INTO quotes (
                guild_id, content, author_id, channel_id, message_id,
                added_by, created_at, reactions_snapshot, author_display, number, author_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id, content, primary, channel_id, message_id,
                added_by, created_at, reactions_snapshot, author_display, number,
                json.dumps(linked),
            ),
        )
        await self.connection.commit()
        quote = await self.get_quote(cursor.lastrowid)
        assert quote is not None
        return quote

    async def get_quote(self, quote_id: int) -> Quote | None:
        cursor = await self.connection.execute(
            "SELECT * FROM quotes WHERE id = ?",
            (quote_id,),
        )
        row = await cursor.fetchone()
        return Quote.from_row(row) if row else None

    async def get_quote_by_number(self, guild_id: int, number: int) -> Quote | None:
        cursor = await self.connection.execute(
            "SELECT * FROM quotes WHERE guild_id = ? AND number = ?",
            (guild_id, number),
        )
        row = await cursor.fetchone()
        return Quote.from_row(row) if row else None

    async def get_quote_by_posted_message(
        self,
        guild_id: int,
        message_id: int,
    ) -> Quote | None:
        cursor = await self.connection.execute(
            "SELECT * FROM quotes WHERE guild_id = ? AND posted_message_id = ?",
            (guild_id, message_id),
        )
        row = await cursor.fetchone()
        return Quote.from_row(row) if row else None

    async def list_quotes(
        self,
        guild_id: int,
        author_id: int | None = None,
        limit: int = 10,
    ) -> list[Quote]:
        if author_id is not None:
            # author_ids is TEXT JSON array; jsonb @> works on Postgres (no sqlite json_each).
            cursor = await self.connection.execute(
                """
                SELECT * FROM quotes
                WHERE guild_id = ?
                  AND (
                    author_id = ?
                    OR COALESCE(author_ids, '[]')::jsonb @> jsonb_build_array(?::bigint)
                  )
                ORDER BY saved_at DESC LIMIT ?
                """,
                (guild_id, author_id, author_id, limit),
            )
        else:
            cursor = await self.connection.execute(
                "SELECT * FROM quotes WHERE guild_id = ? ORDER BY saved_at DESC LIMIT ?",
                (guild_id, limit),
            )
        rows = await cursor.fetchall()
        return [Quote.from_row(r) for r in rows]

    async def random_quote(self, guild_id: int) -> Quote | None:
        cursor = await self.connection.execute(
            "SELECT * FROM quotes WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return Quote.from_row(row) if row else None

    async def count_quotes(self, guild_id: int, author_id: int | None = None) -> int:
        if author_id is not None:
            cursor = await self.connection.execute(
                """
                SELECT COUNT(*) AS c FROM quotes
                WHERE guild_id = ?
                  AND (
                    author_id = ?
                    OR COALESCE(author_ids, '[]')::jsonb @> jsonb_build_array(?::bigint)
                  )
                """,
                (guild_id, author_id, author_id),
            )
        else:
            cursor = await self.connection.execute(
                "SELECT COUNT(*) AS c FROM quotes WHERE guild_id = ?",
                (guild_id,),
            )
        row = await cursor.fetchone()
        return int(row["c"])

    async def list_quote_ids(self, guild_id: int) -> set[int]:
        cursor = await self.connection.execute(
            "SELECT id FROM quotes WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return {int(row["id"]) for row in rows}

    async def set_quote_posted_message(
        self,
        quote_id: int,
        channel_id: int,
        message_id: int,
    ) -> None:
        await self.connection.execute(
            """
            UPDATE quotes
            SET posted_channel_id = ?, posted_message_id = ?
            WHERE id = ?
            """,
            (channel_id, message_id, quote_id),
        )
        await self.connection.commit()

    async def save_quote(self, quote: Quote) -> None:
        await self.connection.execute(
            """
            UPDATE quotes
            SET content = ?, author_id = ?, author_display = ?, created_at = ?, author_ids = ?
            WHERE id = ? AND guild_id = ?
            """,
            (
                quote.content,
                quote.author_id,
                quote.author_display,
                quote.created_at,
                json.dumps(quote.linked_author_ids()),
                quote.id,
                quote.guild_id,
            ),
        )
        await self.connection.commit()

    async def delete_quote(self, quote_id: int, guild_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM quotes WHERE id = ? AND guild_id = ?",
            (quote_id, guild_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def list_quotes_chronological(self, guild_id: int) -> list[Quote]:
        cursor = await self.connection.execute(
            "SELECT * FROM quotes WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        quotes = [Quote.from_row(row) for row in rows]

        def sort_key(quote: Quote) -> tuple[datetime, int]:
            raw = quote.created_at or quote.saved_at or ""
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.replace(tzinfo=None)
                return (parsed, quote.id)
            except ValueError:
                return (datetime.max, quote.id)

        quotes.sort(key=sort_key)
        return quotes

    async def renumber_quotes(self, guild_id: int) -> list[Quote]:
        quotes = await self.list_quotes_chronological(guild_id)
        if not quotes:
            return []
        for index, quote in enumerate(quotes, start=1):
            await self.connection.execute(
                "UPDATE quotes SET number = ? WHERE id = ?",
                (-index, quote.id),
            )
        for index, quote in enumerate(quotes, start=1):
            await self.connection.execute(
                "UPDATE quotes SET number = ? WHERE id = ?",
                (index, quote.id),
            )
            quote.number = index
        await self.connection.commit()
        return quotes

    # --- Custom roles ---

    async def save_custom_role(
        self,
        guild_id: int,
        role_id: int,
        owner_id: int | None,
        kind: str,
        rgb_enabled: bool = False,
        rgb_speed: float = 1.0,
        rgb_hue: float = 0.0,
    ) -> CustomRole:
        await self.ensure_guild(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO custom_roles (
                guild_id, role_id, owner_id, kind, rgb_enabled, rgb_speed, rgb_hue
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, role_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                kind = excluded.kind,
                rgb_enabled = excluded.rgb_enabled,
                rgb_speed = excluded.rgb_speed,
                rgb_hue = excluded.rgb_hue,
                updated_at = datetime('now')
            """,
            (guild_id, role_id, owner_id, kind, int(rgb_enabled), rgb_speed, rgb_hue),
        )
        await self.connection.commit()
        role = await self.get_custom_role_by_role_id(guild_id, role_id)
        assert role is not None
        return role

    async def get_custom_role_by_role_id(
        self, guild_id: int, role_id: int
    ) -> CustomRole | None:
        cursor = await self.connection.execute(
            "SELECT * FROM custom_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        row = await cursor.fetchone()
        return CustomRole.from_row(row) if row else None

    async def get_personal_role(self, guild_id: int, owner_id: int) -> CustomRole | None:
        cursor = await self.connection.execute(
            """
            SELECT * FROM custom_roles
            WHERE guild_id = ? AND owner_id = ? AND kind = 'personal'
            """,
            (guild_id, owner_id),
        )
        row = await cursor.fetchone()
        return CustomRole.from_row(row) if row else None

    async def update_custom_role(self, guild_id: int, role_id: int, **fields: Any) -> CustomRole:
        allowed = {"rgb_enabled", "rgb_speed", "rgb_hue", "owner_id", "kind"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown custom role fields: {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [guild_id, role_id]
        await self.connection.execute(
            f"""
            UPDATE custom_roles SET {assignments}, updated_at = datetime('now')
            WHERE guild_id = ? AND role_id = ?
            """,
            values,
        )
        await self.connection.commit()
        role = await self.get_custom_role_by_role_id(guild_id, role_id)
        assert role is not None
        return role

    async def delete_personal_role_record(self, guild_id: int, owner_id: int) -> bool:
        cursor = await self.connection.execute(
            """
            DELETE FROM custom_roles
            WHERE guild_id = ? AND owner_id = ? AND kind = 'personal'
            """,
            (guild_id, owner_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def delete_custom_role_record(self, guild_id: int, role_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM custom_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def set_birthday_star_role_id(self, guild_id: int, role_id: int | None) -> None:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            "UPDATE guilds SET birthday_star_role_id = ?, updated_at = datetime('now') WHERE guild_id = ?",
            (role_id, guild_id),
        )
        await self.connection.commit()

    async def get_birthday_star_grant(
        self, guild_id: int, user_id: int
    ) -> BirthdayStarGrant | None:
        cursor = await self.connection.execute(
            "SELECT * FROM birthday_star_grants WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return BirthdayStarGrant.from_row(row) if row else None

    async def list_birthday_star_grants(self, guild_id: int) -> list[BirthdayStarGrant]:
        cursor = await self.connection.execute(
            "SELECT * FROM birthday_star_grants WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [BirthdayStarGrant.from_row(row) for row in rows]

    async def save_birthday_star_grant(
        self,
        guild_id: int,
        user_id: int,
        hidden_role_ids: str,
        granted_on: str,
    ) -> BirthdayStarGrant:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            """
            INSERT INTO birthday_star_grants (guild_id, user_id, hidden_role_ids, granted_on)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                hidden_role_ids = excluded.hidden_role_ids,
                granted_on = excluded.granted_on
            """,
            (guild_id, user_id, hidden_role_ids, granted_on),
        )
        await self.connection.commit()
        grant = await self.get_birthday_star_grant(guild_id, user_id)
        assert grant is not None
        return grant

    async def delete_birthday_star_grant(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM birthday_star_grants WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def list_rgb_roles(self) -> list[CustomRole]:
        cursor = await self.connection.execute(
            "SELECT * FROM custom_roles WHERE rgb_enabled = 1",
        )
        rows = await cursor.fetchall()
        return [CustomRole.from_row(r) for r in rows]

    # --- Festivals ---

    def _festival_seed(self, guild_id: int) -> int:
        """Home guild continues from 29; other guilds start at #1."""
        return FESTIVAL_START_NUMBER - 1 if is_home_guild(guild_id) else 0

    async def next_festival_number(self, guild_id: int) -> int:
        seed = self._festival_seed(guild_id)
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(number), ?) + 1 AS n FROM festivals WHERE guild_id = ?",
            (seed, guild_id),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else seed + 1

    async def create_festival(self, guild_id: int, starts_at: str) -> Festival:
        await self.ensure_guild(guild_id)
        number = await self.next_festival_number(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO festivals (guild_id, number, starts_at)
            VALUES (?, ?, ?)
            """,
            (guild_id, number, starts_at),
        )
        await self.connection.commit()
        festival = await self.get_festival(cursor.lastrowid)
        assert festival is not None
        return festival

    async def get_festival(self, festival_id: int) -> Festival | None:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE id = ?",
            (festival_id,),
        )
        row = await cursor.fetchone()
        return Festival.from_row(row) if row else None

    async def get_open_festival(self, guild_id: int) -> Festival | None:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE guild_id = ? AND status = 'open' ORDER BY number DESC LIMIT 1",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return Festival.from_row(row) if row else None

    async def get_festival_by_number(self, guild_id: int, number: int) -> Festival | None:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE guild_id = ? AND number = ?",
            (guild_id, number),
        )
        row = await cursor.fetchone()
        return Festival.from_row(row) if row else None

    async def get_previous_festival(self, guild_id: int, number: int) -> Festival | None:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE guild_id = ? AND number < ? ORDER BY number DESC LIMIT 1",
            (guild_id, number),
        )
        row = await cursor.fetchone()
        return Festival.from_row(row) if row else None

    async def list_guild_festivals(self, guild_id: int) -> list[Festival]:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE guild_id = ? ORDER BY number DESC",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [Festival.from_row(r) for r in rows]

    async def list_open_festivals(self) -> list[Festival]:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE status = 'open' ORDER BY starts_at",
        )
        rows = await cursor.fetchall()
        return [Festival.from_row(r) for r in rows]

    async def list_posted_festivals(self) -> list[Festival]:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE message_id IS NOT NULL ORDER BY id",
        )
        rows = await cursor.fetchall()
        return [Festival.from_row(r) for r in rows]

    async def delete_festival(self, festival_id: int) -> None:
        await self.connection.execute("DELETE FROM festivals WHERE id = ?", (festival_id,))
        await self.connection.commit()

    async def renumber_festivals(self, guild_id: int) -> list[Festival]:
        cursor = await self.connection.execute(
            "SELECT * FROM festivals WHERE guild_id = ? ORDER BY number ASC, id ASC",
            (guild_id,),
        )
        festivals = [Festival.from_row(row) for row in await cursor.fetchall()]
        if not festivals:
            return []
        start_number = self._festival_seed(guild_id) + 1
        for index, festival in enumerate(festivals, start=start_number):
            await self.connection.execute(
                "UPDATE festivals SET number = ? WHERE id = ?",
                (-index, festival.id),
            )
        for index, festival in enumerate(festivals, start=start_number):
            await self.connection.execute(
                "UPDATE festivals SET number = ? WHERE id = ?",
                (index, festival.id),
            )
            festival.number = index
        await self.connection.commit()
        return festivals

    async def update_festival(self, festival_id: int, **fields: Any) -> Festival:
        allowed = {
            "starts_at",
            "channel_id",
            "message_id",
            "winner_user_id",
            "winner_film",
            "status",
            "reminder_sent",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown festival fields: {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [festival_id]
        await self.connection.execute(
            f"UPDATE festivals SET {assignments} WHERE id = ?",
            values,
        )
        await self.connection.commit()
        festival = await self.get_festival(festival_id)
        assert festival is not None
        return festival

    async def upsert_festival_film(
        self,
        festival_id: int,
        user_id: int,
        title: str,
        image_url: str | None = None,
        age_rating: str | None = None,
        runtime_minutes: int | None = None,
        *,
        overwrite_age: bool = False,
    ) -> FestivalFilm:
        age_assign = (
            "age_rating = excluded.age_rating"
            if overwrite_age
            else "age_rating = COALESCE(excluded.age_rating, festival_films.age_rating)"
        )
        await self.connection.execute(
            f"""
            INSERT INTO festival_films (
                festival_id, user_id, title, image_url, age_rating, runtime_minutes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(festival_id, user_id) DO UPDATE SET
                title = excluded.title,
                image_url = CASE
                    WHEN festival_films.title != excluded.title THEN excluded.image_url
                    ELSE COALESCE(excluded.image_url, festival_films.image_url)
                END,
                {age_assign},
                runtime_minutes = CASE
                    WHEN festival_films.title != excluded.title THEN excluded.runtime_minutes
                    ELSE COALESCE(excluded.runtime_minutes, festival_films.runtime_minutes)
                END
            """,
            (festival_id, user_id, title, image_url, age_rating, runtime_minutes),
        )
        await self.connection.commit()
        cursor = await self.connection.execute(
            "SELECT * FROM festival_films WHERE festival_id = ? AND user_id = ?",
            (festival_id, user_id),
        )
        row = await cursor.fetchone()
        assert row is not None
        return FestivalFilm.from_row(row)

    async def remove_festival_film(self, festival_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM festival_films WHERE festival_id = ? AND user_id = ?",
            (festival_id, user_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def get_festival_film(self, festival_id: int, user_id: int) -> FestivalFilm | None:
        cursor = await self.connection.execute(
            "SELECT * FROM festival_films WHERE festival_id = ? AND user_id = ?",
            (festival_id, user_id),
        )
        row = await cursor.fetchone()
        return FestivalFilm.from_row(row) if row else None

    async def list_festival_films(self, festival_id: int) -> list[FestivalFilm]:
        cursor = await self.connection.execute(
            "SELECT * FROM festival_films WHERE festival_id = ? ORDER BY created_at, user_id",
            (festival_id,),
        )
        rows = await cursor.fetchall()
        return [FestivalFilm.from_row(r) for r in rows]

    async def upsert_festival_rating(
        self,
        festival_id: int,
        user_id: int,
        score: int,
    ) -> None:
        cursor = await self.connection.execute(
            """
            SELECT score FROM festival_ratings
            WHERE festival_id = ? AND user_id = ?
            """,
            (festival_id, user_id),
        )
        previous = await cursor.fetchone()
        previous_score = int(previous["score"]) if previous is not None else None
        await self.connection.execute(
            """
            INSERT INTO festival_ratings (festival_id, user_id, score)
            VALUES (?, ?, ?)
            ON CONFLICT(festival_id, user_id) DO UPDATE SET score = excluded.score
            """,
            (festival_id, user_id, score),
        )
        if previous_score != score:
            await self.connection.execute(
                """
                INSERT INTO festival_rating_logs (festival_id, user_id, score)
                VALUES (?, ?, ?)
                """,
                (festival_id, user_id, score),
            )
        await self.connection.commit()

    async def list_festival_rating_logs(self, festival_id: int) -> list[FestivalRatingLog]:
        cursor = await self.connection.execute(
            """
            SELECT * FROM festival_rating_logs
            WHERE festival_id = ?
            ORDER BY created_at, id
            """,
            (festival_id,),
        )
        rows = await cursor.fetchall()
        return [FestivalRatingLog.from_row(r) for r in rows]

    async def list_festival_ratings(self, festival_id: int) -> list[tuple[int, int]]:
        cursor = await self.connection.execute(
            """
            SELECT user_id, score FROM festival_ratings
            WHERE festival_id = ?
            ORDER BY created_at, user_id
            """,
            (festival_id,),
        )
        rows = await cursor.fetchall()
        return [(int(row["user_id"]), int(row["score"])) for row in rows]

    async def open_festival_voice_segment(
        self,
        festival_id: int,
        user_id: int,
        channel_id: int,
        started_at: str,
    ) -> None:
        existing = await self.get_open_festival_voice_segment(festival_id, user_id)
        if existing is not None:
            return
        await self.connection.execute(
            """
            INSERT INTO festival_voice_segments
                (festival_id, user_id, channel_id, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (festival_id, user_id, channel_id, started_at),
        )
        await self.connection.commit()

    async def get_open_festival_voice_segment(
        self,
        festival_id: int,
        user_id: int,
    ):
        cursor = await self.connection.execute(
            """
            SELECT * FROM festival_voice_segments
            WHERE festival_id = ? AND user_id = ? AND ended_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (festival_id, user_id),
        )
        return await cursor.fetchone()

    async def list_open_festival_voice_segments(self, festival_id: int) -> list:
        cursor = await self.connection.execute(
            """
            SELECT * FROM festival_voice_segments
            WHERE festival_id = ? AND ended_at IS NULL
            """,
            (festival_id,),
        )
        return list(await cursor.fetchall())

    async def close_festival_voice_segment(self, segment_id: int, ended_at: str) -> None:
        await self.connection.execute(
            """
            UPDATE festival_voice_segments
            SET ended_at = ?
            WHERE id = ? AND ended_at IS NULL
            """,
            (ended_at, segment_id),
        )
        await self.connection.commit()

    async def close_festival_voice_segments(self, festival_id: int, ended_at: str) -> None:
        await self.connection.execute(
            """
            UPDATE festival_voice_segments
            SET ended_at = ?
            WHERE festival_id = ? AND ended_at IS NULL
            """,
            (ended_at, festival_id),
        )
        await self.connection.commit()

    async def close_all_open_festival_voice_segments(self, ended_at: str) -> None:
        await self.connection.execute(
            """
            UPDATE festival_voice_segments
            SET ended_at = ?
            WHERE ended_at IS NULL
            """,
            (ended_at,),
        )
        await self.connection.commit()

    async def festival_voice_user_seconds(
        self,
        festival_id: int,
        user_id: int,
        now_iso: str,
    ) -> int:
        cursor = await self.connection.execute(
            """
            SELECT started_at, ended_at FROM festival_voice_segments
            WHERE festival_id = ? AND user_id = ?
            """,
            (festival_id, user_id),
        )
        total = 0
        for row in await cursor.fetchall():
            total += self._segment_seconds(row["started_at"], row["ended_at"], now_iso)
        return total

    async def festival_voice_stats(
        self,
        festival_id: int,
        now_iso: str,
    ) -> list[FestivalVoiceStats]:
        cursor = await self.connection.execute(
            """
            SELECT user_id, started_at, ended_at FROM festival_voice_segments
            WHERE festival_id = ?
            ORDER BY user_id, started_at, id
            """,
            (festival_id,),
        )
        by_user: dict[int, list[int]] = {}
        for row in await cursor.fetchall():
            user_id = int(row["user_id"])
            seconds = self._segment_seconds(row["started_at"], row["ended_at"], now_iso)
            by_user.setdefault(user_id, []).append(seconds)
        return [
            FestivalVoiceStats(
                user_id=user_id,
                total_seconds=sum(parts),
                max_continuous_seconds=max(parts) if parts else 0,
            )
            for user_id, parts in by_user.items()
            if sum(parts) > 0
        ]

    @staticmethod
    def _segment_seconds(started_at: str, ended_at: str | None, now_iso: str) -> int:
        try:
            start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            end_raw = ended_at if ended_at is not None else now_iso
            end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        except ValueError:
            return 0
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, int((end - start).total_seconds()))

    async def festival_rating_stats(self, festival_id: int) -> tuple[float | None, int]:
        cursor = await self.connection.execute(
            """
            SELECT AVG(score) AS average, COUNT(*) AS votes
            FROM festival_ratings
            WHERE festival_id = ?
            """,
            (festival_id,),
        )
        row = await cursor.fetchone()
        if row is None or not row["votes"]:
            return None, 0
        return float(row["average"]), int(row["votes"])

    async def add_blocked_film(self, guild_id: int, title_key: str, title: str) -> bool:
        await self.ensure_guild(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT OR IGNORE INTO festival_blocked_films (guild_id, title_key, title)
            VALUES (?, ?, ?)
            """,
            (guild_id, title_key, title),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def is_film_blocked(self, guild_id: int, title_key: str) -> bool:
        cursor = await self.connection.execute(
            "SELECT 1 FROM festival_blocked_films WHERE guild_id = ? AND title_key = ?",
            (guild_id, title_key),
        )
        return await cursor.fetchone() is not None

    async def remove_blocked_film(self, guild_id: int, title_key: str) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM festival_blocked_films WHERE guild_id = ? AND title_key = ?",
            (guild_id, title_key),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    # --- Tarot ---

    async def list_tarot_draws(self, guild_id: int, user_id: int) -> list[tuple[str, str]]:
        cursor = await self.connection.execute(
            "SELECT card_id, drawn_at FROM tarot_draws WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return [(row["card_id"], row["drawn_at"]) for row in await cursor.fetchall()]

    async def add_tarot_draw(
        self,
        guild_id: int,
        user_id: int,
        card_id: str,
        reversed_: bool,
        drawn_at: str,
    ) -> None:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            """
            INSERT OR REPLACE INTO tarot_draws (guild_id, user_id, card_id, reversed, drawn_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, card_id, int(reversed_), drawn_at),
        )
        await self.connection.commit()

    async def clear_tarot_draws(self, guild_id: int, user_id: int) -> None:
        await self.connection.execute(
            "DELETE FROM tarot_draws WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await self.connection.commit()

    # --- Telegram channels ---

    async def next_tg_channel_number(self, guild_id: int) -> int:
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM tg_channels WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 1

    async def add_tg_channel(
        self,
        guild_id: int,
        user_id: int,
        title: str,
        url: str,
        image_url: str | None,
        description: str | None = None,
        telegram_chat_id: int | None = None,
    ) -> TgChannel:
        await self.ensure_guild(guild_id)
        number = await self.next_tg_channel_number(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO tg_channels (
                guild_id, user_id, number, title, url, image_url, description, telegram_chat_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                user_id,
                number,
                title,
                url,
                image_url,
                description,
                telegram_chat_id,
            ),
        )
        await self.connection.commit()
        channel = await self.get_tg_channel(cursor.lastrowid)
        assert channel is not None
        return channel

    async def get_tg_channel(self, channel_id: int) -> TgChannel | None:
        cursor = await self.connection.execute(
            "SELECT * FROM tg_channels WHERE id = ?",
            (channel_id,),
        )
        row = await cursor.fetchone()
        return TgChannel.from_row(row) if row else None

    async def get_tg_channel_by_telegram_id(
        self,
        guild_id: int,
        telegram_chat_id: int,
    ) -> TgChannel | None:
        cursor = await self.connection.execute(
            """
            SELECT * FROM tg_channels
            WHERE guild_id = ? AND telegram_chat_id = ?
            LIMIT 1
            """,
            (guild_id, telegram_chat_id),
        )
        row = await cursor.fetchone()
        return TgChannel.from_row(row) if row else None

    async def get_tg_channel_by_number(self, guild_id: int, number: int) -> TgChannel | None:
        cursor = await self.connection.execute(
            "SELECT * FROM tg_channels WHERE guild_id = ? AND number = ?",
            (guild_id, number),
        )
        row = await cursor.fetchone()
        return TgChannel.from_row(row) if row else None

    async def list_tg_channels(self, guild_id: int) -> list[TgChannel]:
        cursor = await self.connection.execute(
            "SELECT * FROM tg_channels WHERE guild_id = ? ORDER BY number",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [TgChannel.from_row(r) for r in rows]

    async def delete_tg_channel(self, channel_id: int, guild_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM tg_channels WHERE id = ? AND guild_id = ?",
            (channel_id, guild_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def update_tg_channel_meta(
        self,
        channel_id: int,
        guild_id: int,
        title: str,
        image_url: str | None,
        description: str | None = None,
        *,
        url: str | None = None,
        telegram_chat_id: int | None = None,
    ) -> None:
        fields = {
            "title": title,
            "image_url": image_url,
            "description": description,
        }
        if url is not None:
            fields["url"] = url
        if telegram_chat_id is not None:
            fields["telegram_chat_id"] = telegram_chat_id
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [channel_id, guild_id]
        await self.connection.execute(
            f"""
            UPDATE tg_channels
            SET {assignments}
            WHERE id = ? AND guild_id = ?
            """,
            values,
        )
        await self.connection.commit()

    async def renumber_tg_channels(self, guild_id: int) -> list[TgChannel]:
        channels = await self.list_tg_channels(guild_id)
        if not channels:
            return []
        return await self.renumber_tg_channels_ordered(guild_id, [channel.id for channel in channels])

    async def renumber_tg_channels_ordered(self, guild_id: int, ordered_ids: list[int]) -> list[TgChannel]:
        if not ordered_ids:
            return []
        for index, channel_id in enumerate(ordered_ids, start=1):
            await self.connection.execute(
                "UPDATE tg_channels SET number = ? WHERE id = ? AND guild_id = ?",
                (-index, channel_id, guild_id),
            )
        for index, channel_id in enumerate(ordered_ids, start=1):
            await self.connection.execute(
                "UPDATE tg_channels SET number = ? WHERE id = ? AND guild_id = ?",
                (index, channel_id, guild_id),
            )
        await self.connection.commit()
        return await self.list_tg_channels(guild_id)

    async def set_tgk_board_message_id(self, guild_id: int, message_id: int | None) -> None:
        ids = [message_id] if message_id is not None else []
        await self.set_tgk_board_message_ids(guild_id, ids)

    async def get_tgk_board_message_ids(self, guild_id: int) -> list[int]:
        config = await self.get_guild(guild_id)
        if config is None:
            return []
        if config.tgk_board_message_ids:
            return list(config.tgk_board_message_ids)
        if config.tgk_board_message_id is not None:
            return [config.tgk_board_message_id]
        return []

    async def set_tgk_board_message_ids(self, guild_id: int, message_ids: list[int]) -> None:
        await self.ensure_guild(guild_id)
        cleaned = [int(message_id) for message_id in message_ids if message_id]
        primary = cleaned[0] if cleaned else None
        await self.connection.execute(
            """
            UPDATE guilds
            SET tgk_board_message_ids = ?,
                tgk_board_message_id = ?,
                updated_at = datetime('now')
            WHERE guild_id = ?
            """,
            (json.dumps(cleaned), primary, guild_id),
        )
        await self.connection.commit()

    async def get_santa_event(self, event_id: int) -> SantaEvent | None:
        cursor = await self.connection.execute(
            "SELECT * FROM santa_events WHERE id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        return SantaEvent.from_row(row) if row else None

    async def get_active_santa_event(self, guild_id: int) -> SantaEvent | None:
        cursor = await self.connection.execute(
            """
            SELECT * FROM santa_events
            WHERE guild_id = ?
            ORDER BY number DESC
            LIMIT 1
            """,
            (guild_id,),
        )
        row = await cursor.fetchone()
        return SantaEvent.from_row(row) if row else None

    async def list_open_santa_events(self) -> list[SantaEvent]:
        cursor = await self.connection.execute(
            "SELECT * FROM santa_events WHERE drawn = 0"
        )
        rows = await cursor.fetchall()
        return [SantaEvent.from_row(row) for row in rows]

    async def next_santa_number(self, guild_id: int) -> int:
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM santa_events WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["n"]) if row else 1

    async def create_santa_event(self, guild_id: int) -> SantaEvent:
        number = await self.next_santa_number(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO santa_events (guild_id, number)
            VALUES (?, ?)
            """,
            (guild_id, number),
        )
        await self.connection.commit()
        event = await self.get_santa_event(cursor.lastrowid)
        assert event is not None
        return event

    async def set_santa_event_message(
        self,
        event_id: int,
        channel_id: int,
        message_id: int,
    ) -> None:
        await self.connection.execute(
            """
            UPDATE santa_events
            SET channel_id = ?, message_id = ?
            WHERE id = ?
            """,
            (channel_id, message_id, event_id),
        )
        await self.connection.commit()

    async def mark_santa_drawn(self, event_id: int) -> None:
        await self.connection.execute(
            "UPDATE santa_events SET drawn = 1 WHERE id = ?",
            (event_id,),
        )
        await self.connection.commit()

    async def upsert_santa_entry(
        self,
        event_id: int,
        user_id: int,
        address: str,
        wishlist_url: str,
    ) -> SantaEntry:
        await self.connection.execute(
            """
            INSERT INTO santa_entries (event_id, user_id, address, wishlist_url, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(event_id, user_id) DO UPDATE SET
                address = excluded.address,
                wishlist_url = excluded.wishlist_url,
                updated_at = datetime('now')
            """,
            (event_id, user_id, address, wishlist_url),
        )
        await self.connection.commit()
        cursor = await self.connection.execute(
            "SELECT * FROM santa_entries WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        row = await cursor.fetchone()
        assert row is not None
        return SantaEntry.from_row(row)

    async def get_santa_entry(self, event_id: int, user_id: int) -> SantaEntry | None:
        cursor = await self.connection.execute(
            "SELECT * FROM santa_entries WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        row = await cursor.fetchone()
        return SantaEntry.from_row(row) if row else None

    async def delete_santa_entry(self, event_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM santa_entries WHERE event_id = ? AND user_id = ?",
            (event_id, user_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def list_santa_entries(self, event_id: int) -> list[SantaEntry]:
        cursor = await self.connection.execute(
            "SELECT * FROM santa_entries WHERE event_id = ? ORDER BY updated_at ASC",
            (event_id,),
        )
        rows = await cursor.fetchall()
        return [SantaEntry.from_row(row) for row in rows]

    async def save_santa_assignments(
        self,
        event_id: int,
        pairs: list[tuple[int, int]],
    ) -> None:
        await self.connection.execute(
            "DELETE FROM santa_assignments WHERE event_id = ?",
            (event_id,),
        )
        await self.connection.executemany(
            """
            INSERT INTO santa_assignments (event_id, giver_id, receiver_id)
            VALUES (?, ?, ?)
            """,
            [(event_id, giver_id, receiver_id) for giver_id, receiver_id in pairs],
        )
        await self.connection.commit()

    async def list_santa_assignments(self, event_id: int) -> list[SantaAssignment]:
        cursor = await self.connection.execute(
            "SELECT * FROM santa_assignments WHERE event_id = ?",
            (event_id,),
        )
        rows = await cursor.fetchall()
        return [SantaAssignment.from_row(row) for row in rows]

    async def add_voice_snippet(self, guild_id: int, user_id: int, text: str) -> None:
        await self.connection.execute(
            """
            INSERT INTO voice_snippets (guild_id, user_id, text)
            VALUES (?, ?, ?)
            """,
            (guild_id, user_id, text),
        )
        await self.connection.commit()

    async def add_daily_text_log(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        source: str,
        text: str,
        day: str,
    ) -> None:
        await self.connection.execute(
            """
            INSERT INTO daily_text_logs
                (guild_id, user_id, channel_id, source, text, day)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, int(channel_id or 0), source, text, day),
        )
        await self.connection.commit()

    async def bump_member_gender_scores(
        self,
        guild_id: int,
        user_id: int,
        score_f: int,
        score_m: int,
    ) -> str:
        """Accumulate gender evidence; return current gender code f/m/u."""
        if score_f <= 0 and score_m <= 0:
            return await self.get_member_gender(guild_id, user_id)
        await self.connection.execute(
            """
            INSERT INTO member_profiles (guild_id, user_id, score_f, score_m, gender)
            VALUES (?, ?, ?, ?, 'u')
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                score_f = member_profiles.score_f + excluded.score_f,
                score_m = member_profiles.score_m + excluded.score_m,
                updated_at = datetime('now')
            """,
            (guild_id, user_id, max(0, score_f), max(0, score_m)),
        )
        cursor = await self.connection.execute(
            """
            SELECT score_f, score_m FROM member_profiles
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        from bot.utils.gender import decide_gender

        gender = decide_gender(int(row["score_f"]), int(row["score_m"])) if row else "u"
        await self.connection.execute(
            """
            UPDATE member_profiles SET gender = ?
            WHERE guild_id = ? AND user_id = ?
            """,
            (gender, guild_id, user_id),
        )
        await self.connection.commit()
        return gender

    async def get_member_gender(self, guild_id: int, user_id: int) -> str:
        cursor = await self.connection.execute(
            """
            SELECT gender FROM member_profiles
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return "u"
        value = str(row["gender"] or "u")
        return value if value in {"f", "m", "u"} else "u"

    async def list_daily_text_log_users(self, guild_id: int, day: str) -> list[int]:
        cursor = await self.connection.execute(
            """
            SELECT DISTINCT user_id FROM daily_text_logs
            WHERE guild_id = ? AND day = ?
            """,
            (guild_id, day),
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    async def list_daily_text_log_days(
        self,
        guild_id: int,
        *,
        before_day: str,
    ) -> list[str]:
        cursor = await self.connection.execute(
            """
            SELECT DISTINCT day FROM daily_text_logs
            WHERE guild_id = ? AND day < ?
            ORDER BY day ASC
            """,
            (guild_id, before_day),
        )
        rows = await cursor.fetchall()
        return [str(row["day"]) for row in rows]

    async def list_daily_text_logs(
        self,
        guild_id: int,
        user_id: int,
        day: str,
        *,
        limit: int = 400,
    ) -> list[DailyTextLog]:
        cursor = await self.connection.execute(
            """
            SELECT * FROM daily_text_logs
            WHERE guild_id = ? AND user_id = ? AND day = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (guild_id, user_id, day, limit),
        )
        rows = await cursor.fetchall()
        return [DailyTextLog.from_row(row) for row in rows]

    async def list_daily_text_logs_for_guild_day(
        self,
        guild_id: int,
        day: str,
        *,
        source: str | None = "voice",
        limit: int = 5000,
    ) -> list[DailyTextLog]:
        """Guild-scoped day transcript (never crosses other servers)."""
        if source:
            cursor = await self.connection.execute(
                """
                SELECT * FROM daily_text_logs
                WHERE guild_id = ? AND day = ? AND source = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (guild_id, day, source, limit),
            )
        else:
            cursor = await self.connection.execute(
                """
                SELECT * FROM daily_text_logs
                WHERE guild_id = ? AND day = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (guild_id, day, limit),
            )
        rows = await cursor.fetchall()
        return [DailyTextLog.from_row(row) for row in rows]

    async def list_voice_import_logs_for_guild_day(
        self,
        guild_id: int,
        day: str,
        *,
        listen_channel_ids: list[int] | None = None,
        limit: int = 5000,
    ) -> list[DailyTextLog]:
        """Voice STT + text-in-voice chat for listen channel(s) that day."""
        cursor = await self.connection.execute(
            """
            SELECT DISTINCT channel_id FROM daily_text_logs
            WHERE guild_id = ? AND day = ? AND source = 'voice' AND channel_id > 0
            """,
            (guild_id, day),
        )
        channel_ids: set[int] = {
            int(row["channel_id"]) for row in await cursor.fetchall()
        }
        for cid in listen_channel_ids or ():
            if cid:
                channel_ids.add(int(cid))

        if not channel_ids:
            return await self.list_daily_text_logs_for_guild_day(
                guild_id, day, source="voice", limit=limit
            )

        placeholders = ", ".join("?" for _ in channel_ids)
        ids = list(channel_ids)
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM daily_text_logs
            WHERE guild_id = ? AND day = ?
              AND (
                source = 'voice'
                OR (source = 'chat' AND channel_id IN ({placeholders}))
              )
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (guild_id, day, *ids, limit),
        )
        rows = await cursor.fetchall()
        return [DailyTextLog.from_row(row) for row in rows]

    async def delete_daily_text_logs(
        self,
        guild_id: int,
        user_id: int,
        day: str,
    ) -> int:
        cursor = await self.connection.execute(
            """
            DELETE FROM daily_text_logs
            WHERE guild_id = ? AND user_id = ? AND day = ?
            """,
            (guild_id, user_id, day),
        )
        await self.connection.commit()
        return cursor.rowcount

    async def list_pending_voice_snippet_users(
        self,
        guild_id: int,
    ) -> list[int]:
        cursor = await self.connection.execute(
            """
            SELECT DISTINCT user_id FROM voice_snippets
            WHERE guild_id = ? AND processed = 0
            """,
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    async def list_pending_voice_snippets(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 80,
    ) -> list[VoiceSnippet]:
        cursor = await self.connection.execute(
            """
            SELECT * FROM voice_snippets
            WHERE guild_id = ? AND user_id = ? AND processed = 0
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        )
        rows = await cursor.fetchall()
        return [VoiceSnippet.from_row(row) for row in rows]

    async def mark_voice_snippets_processed(self, snippet_ids: list[int]) -> None:
        if not snippet_ids:
            return
        placeholders = ",".join("?" for _ in snippet_ids)
        await self.connection.execute(
            f"UPDATE voice_snippets SET processed = 1 WHERE id IN ({placeholders})",
            snippet_ids,
        )
        await self.connection.commit()

    async def add_voice_dossier_entries(
        self,
        guild_id: int,
        user_id: int,
        facts: list[str],
        source_date: str,
    ) -> None:
        if not facts:
            return
        await self.connection.executemany(
            """
            INSERT INTO voice_dossier (guild_id, user_id, fact, source_date)
            VALUES (?, ?, ?, ?)
            """,
            [(guild_id, user_id, fact, source_date) for fact in facts],
        )
        await self.connection.commit()

    async def list_voice_dossier_users(self, guild_id: int) -> list[int]:
        cursor = await self.connection.execute(
            """
            SELECT DISTINCT user_id FROM voice_dossier
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    async def replace_voice_dossier(
        self,
        guild_id: int,
        user_id: int,
        facts: list[str],
        *,
        source_date: str,
    ) -> None:
        await self.connection.execute(
            "DELETE FROM voice_dossier WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        if facts:
            await self.connection.executemany(
                """
                INSERT INTO voice_dossier (guild_id, user_id, fact, source_date)
                VALUES (?, ?, ?, ?)
                """,
                [(guild_id, user_id, fact, source_date) for fact in facts],
            )
        await self.connection.commit()

    async def list_voice_dossier(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 8,
    ) -> list[VoiceDossierEntry]:
        cursor = await self.connection.execute(
            """
            SELECT * FROM voice_dossier
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        )
        rows = await cursor.fetchall()
        return [VoiceDossierEntry.from_row(row) for row in rows]

    async def increment_voice_swears(
        self,
        guild_id: int,
        user_id: int,
        day: str,
        amount: int,
    ) -> None:
        if amount <= 0:
            return
        await self.connection.execute(
            """
            INSERT INTO voice_swear_stats (guild_id, user_id, day, count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, day) DO UPDATE SET
                count = voice_swear_stats.count + excluded.count
            """,
            (guild_id, user_id, day, amount),
        )
        await self.connection.commit()

    async def sum_voice_swears(
        self,
        guild_id: int,
        user_id: int,
        *,
        since_day: str | None = None,
    ) -> int:
        if since_day:
            cursor = await self.connection.execute(
                """
                SELECT COALESCE(SUM(count), 0) AS c FROM voice_swear_stats
                WHERE guild_id = ? AND user_id = ? AND day >= ?
                """,
                (guild_id, user_id, since_day),
            )
        else:
            cursor = await self.connection.execute(
                """
                SELECT COALESCE(SUM(count), 0) AS c FROM voice_swear_stats
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def cleanup_old_voice_snippets(self, *, keep_days: int = 14) -> int:
        cursor = await self.connection.execute(
            """
            DELETE FROM voice_snippets
            WHERE processed = 1
              AND created_at < datetime('now', ?)
            """,
            (f"-{keep_days} days",),
        )
        await self.connection.commit()
        return cursor.rowcount

    # --- Voice listen mute ---

    async def is_voice_listen_muted(self, guild_id: int, user_id: int) -> bool:
        cursor = await self.connection.execute(
            "SELECT 1 FROM voice_listen_muted WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cursor.fetchone() is not None

    async def toggle_voice_listen_muted(self, guild_id: int, user_id: int) -> bool:
        """Toggle mute; returns True if user is muted after the call."""
        await self.ensure_guild(guild_id)
        if await self.is_voice_listen_muted(guild_id, user_id):
            await self.connection.execute(
                "DELETE FROM voice_listen_muted WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.connection.commit()
            return False
        await self.connection.execute(
            "INSERT INTO voice_listen_muted (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        await self.connection.commit()
        return True

    async def list_voice_listen_muted(self, guild_id: int) -> list[int]:
        cursor = await self.connection.execute(
            "SELECT user_id FROM voice_listen_muted WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    async def list_all_voice_listen_muted(self) -> list[tuple[int, int]]:
        cursor = await self.connection.execute(
            "SELECT guild_id, user_id FROM voice_listen_muted"
        )
        rows = await cursor.fetchall()
        return [(int(row["guild_id"]), int(row["user_id"])) for row in rows]
