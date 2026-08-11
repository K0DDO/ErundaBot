"""Async SQLite database layer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

from .models import (
    GUILD_CONFIG_FIELDS,
    Birthday,
    CustomRole,
    Event,
    GuildConfig,
    Proposal,
    Quote,
)

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
    birthday_board_message_id INTEGER,
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
    author_display TEXT,
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

CREATE TABLE IF NOT EXISTS birthday_notifications (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('announce', 'reminder')),
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
        await self._migrate()
        await self._db.commit()
        log.info("Database ready at %s", self.path)

    async def _migrate(self) -> None:
        """Add columns to existing databases."""
        migrations = (
            "ALTER TABLE guilds ADD COLUMN birthday_board_message_id INTEGER",
            "ALTER TABLE quotes ADD COLUMN author_display TEXT",
        )
        for sql in migrations:
            try:
                await self._db.execute(sql)
            except Exception:
                pass  # column already exists

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
    ) -> Birthday:
        await self.ensure_guild(guild_id)
        await self.connection.execute(
            """
            INSERT INTO birthdays (guild_id, user_id, day, month, year)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                day = excluded.day,
                month = excluded.month,
                year = excluded.year
            """,
            (guild_id, user_id, day, month, year),
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
    ) -> None:
        await self.connection.execute(
            """
            INSERT OR IGNORE INTO birthday_notifications
                (guild_id, user_id, event_date, kind)
            VALUES (?, ?, ?, ?)
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
                count = count + excluded.count
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
                count = MAX(0, count + excluded.count)
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
            SET ended_at = ?, duration_seconds = CAST(
                (julianday(?) - julianday(started_at)) * 86400 AS INTEGER
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
                duration_seconds = CAST(
                    (julianday(?) - julianday(started_at)) * 86400 AS INTEGER
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
                duration_seconds = CAST(
                    (julianday(?) - julianday(started_at)) * 86400 AS INTEGER
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
        max_participants: int | None,
        channel_id: int | None,
    ) -> Event:
        await self.ensure_guild(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO events (
                guild_id, title, description, starts_at,
                max_participants, channel_id, organizer_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, title, description, starts_at, max_participants, channel_id, organizer_id),
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
        allowed = {"title", "description", "starts_at", "max_participants", "channel_id", "message_id", "status"}
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
                "SELECT * FROM events WHERE guild_id = ? AND status = ? ORDER BY starts_at",
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
    ) -> Quote:
        await self.ensure_guild(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO quotes (
                guild_id, content, author_id, channel_id, message_id,
                added_by, created_at, reactions_snapshot, author_display
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id, content, author_id, channel_id, message_id,
                added_by, created_at, reactions_snapshot, author_display,
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

    async def list_quotes(
        self,
        guild_id: int,
        author_id: int | None = None,
        limit: int = 10,
    ) -> list[Quote]:
        if author_id is not None:
            cursor = await self.connection.execute(
                """
                SELECT * FROM quotes WHERE guild_id = ? AND author_id = ?
                ORDER BY saved_at DESC LIMIT ?
                """,
                (guild_id, author_id, limit),
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
                "SELECT COUNT(*) AS c FROM quotes WHERE guild_id = ? AND author_id = ?",
                (guild_id, author_id),
            )
        else:
            cursor = await self.connection.execute(
                "SELECT COUNT(*) AS c FROM quotes WHERE guild_id = ?",
                (guild_id,),
            )
        row = await cursor.fetchone()
        return int(row["c"])

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

    async def delete_custom_role_record(self, guild_id: int, role_id: int) -> bool:
        cursor = await self.connection.execute(
            "DELETE FROM custom_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
        await self.connection.commit()
        return cursor.rowcount > 0

    async def list_rgb_roles(self) -> list[CustomRole]:
        cursor = await self.connection.execute(
            "SELECT * FROM custom_roles WHERE rgb_enabled = 1",
        )
        rows = await cursor.fetchall()
        return [CustomRole.from_row(r) for r in rows]

    # --- Proposals ---

    async def next_proposal_number(self, guild_id: int) -> int:
        cursor = await self.connection.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM proposals WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["n"])

    async def create_proposal(
        self,
        guild_id: int,
        number: int,
        content: str,
        author_id: int,
        ends_at: str,
        channel_id: int | None,
        action_type: str | None = None,
        action_payload: str | None = None,
    ) -> Proposal:
        await self.ensure_guild(guild_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO proposals (
                guild_id, number, content, author_id, ends_at,
                channel_id, action_type, action_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, number, content, author_id, ends_at, channel_id, action_type, action_payload),
        )
        await self.connection.commit()
        proposal = await self.get_proposal(cursor.lastrowid)
        assert proposal is not None
        return proposal

    async def get_proposal(self, proposal_id: int) -> Proposal | None:
        cursor = await self.connection.execute(
            "SELECT * FROM proposals WHERE id = ?",
            (proposal_id,),
        )
        row = await cursor.fetchone()
        return Proposal.from_row(row) if row else None

    async def get_proposal_by_message(self, message_id: int) -> Proposal | None:
        cursor = await self.connection.execute(
            "SELECT * FROM proposals WHERE message_id = ?",
            (message_id,),
        )
        row = await cursor.fetchone()
        return Proposal.from_row(row) if row else None

    async def update_proposal(self, proposal_id: int, **fields: Any) -> Proposal:
        allowed = {"status", "message_id", "channel_id", "content", "ends_at"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown proposal fields: {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [proposal_id]
        await self.connection.execute(
            f"UPDATE proposals SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            values,
        )
        await self.connection.commit()
        proposal = await self.get_proposal(proposal_id)
        assert proposal is not None
        return proposal

    async def list_proposals(
        self,
        guild_id: int,
        status: str | None = None,
        limit: int = 20,
    ) -> list[Proposal]:
        if status:
            cursor = await self.connection.execute(
                """
                SELECT * FROM proposals WHERE guild_id = ? AND status = ?
                ORDER BY number DESC LIMIT ?
                """,
                (guild_id, status, limit),
            )
        else:
            cursor = await self.connection.execute(
                "SELECT * FROM proposals WHERE guild_id = ? ORDER BY number DESC LIMIT ?",
                (guild_id, limit),
            )
        rows = await cursor.fetchall()
        return [Proposal.from_row(r) for r in rows]

    async def list_open_proposals(self) -> list[Proposal]:
        cursor = await self.connection.execute(
            "SELECT * FROM proposals WHERE status = 'open' ORDER BY ends_at",
        )
        rows = await cursor.fetchall()
        return [Proposal.from_row(r) for r in rows]

    async def set_proposal_vote(
        self, proposal_id: int, user_id: int, vote: str
    ) -> None:
        await self.connection.execute(
            """
            INSERT INTO proposal_votes (proposal_id, user_id, vote)
            VALUES (?, ?, ?)
            ON CONFLICT(proposal_id, user_id) DO UPDATE SET
                vote = excluded.vote,
                updated_at = datetime('now')
            """,
            (proposal_id, user_id, vote),
        )
        await self.connection.commit()

    async def get_proposal_vote(self, proposal_id: int, user_id: int) -> str | None:
        cursor = await self.connection.execute(
            "SELECT vote FROM proposal_votes WHERE proposal_id = ? AND user_id = ?",
            (proposal_id, user_id),
        )
        row = await cursor.fetchone()
        return row["vote"] if row else None

    async def count_proposal_votes(self, proposal_id: int) -> tuple[int, int]:
        cursor = await self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN vote = 'yes' THEN 1 ELSE 0 END) AS yes_count,
                SUM(CASE WHEN vote = 'no' THEN 1 ELSE 0 END) AS no_count
            FROM proposal_votes WHERE proposal_id = ?
            """,
            (proposal_id,),
        )
        row = await cursor.fetchone()
        return int(row["yes_count"] or 0), int(row["no_count"] or 0)
