#!/usr/bin/env python3
"""One-shot SQLite → Postgres migration for Erunda.

Usage (on VPS, with postgres up and bot stopped):

  export DATABASE_URL=postgresql://erunda:erunda@localhost:5432/erunda
  python scripts/migrate_sqlite_to_postgres.py [--sqlite ./data/erunda.db]

Idempotent for tables with natural primary keys: uses ON CONFLICT DO NOTHING.
Serial tables clear+copy only if target is empty (or --force).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import asyncpg

# Ensure repo root on path when run as script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.database.database import Database  # noqa: E402


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def copy_table(
    sqlite_conn: sqlite3.Connection,
    pg: asyncpg.Connection,
    table: str,
    *,
    force: bool,
) -> int:
    cur = sqlite_conn.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    if not rows:
        return 0
    cols = [d[0] for d in cur.description]
    count = await pg.fetchval(f"SELECT COUNT(*) FROM {_quote_ident(table)}")
    if count and not force:
        print(f"  skip {table}: already has {count} rows (use --force)")
        return 0
    if count and force:
        await pg.execute(f"TRUNCATE TABLE {_quote_ident(table)} CASCADE")
    col_list = ", ".join(_quote_ident(c) for c in cols)
    placeholders = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
    sql = (
        f"INSERT INTO {_quote_ident(table)} ({col_list}) "
        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    )
    inserted = 0
    for row in rows:
        values = []
        for v in row:
            values.append(v)
        try:
            status = await pg.execute(sql, *values)
            if status.endswith("1"):
                inserted += 1
        except Exception as exc:
            print(f"  warn {table}: {exc}")
    # Reset serial sequences when we copied id columns.
    if "id" in cols:
        await pg.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table}', 'id'),
                COALESCE((SELECT MAX(id) FROM {_quote_ident(table)}), 1),
                true
            )
            """
        )
    return inserted


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sqlite",
        default=os.getenv("DATABASE_PATH", "./data/erunda.db"),
        help="Path to erunda.db",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", ""),
        help="Postgres DATABASE_URL",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate target tables before copy",
    )
    args = parser.parse_args()
    sqlite_path = Path(args.sqlite)
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")
    if not args.dsn:
        raise SystemExit("DATABASE_URL / --dsn required")

    # Ensure PG schema exists.
    db = Database(args.dsn)
    await db.connect()
    await db.close()

    sqlite_conn = sqlite3.connect(str(sqlite_path))
    sqlite_conn.row_factory = sqlite3.Row
    tables = [
        r[0]
        for r in sqlite_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    # Prefer parent tables first.
    preferred = [
        "guilds",
        "users",
        "birthdays",
        "message_statistics",
        "voice_sessions",
        "reaction_statistics",
        "events",
        "event_participants",
        "event_notifications",
        "quotes",
        "custom_roles",
        "proposals",
        "proposal_votes",
        "birthday_star_grants",
        "birthday_notifications",
        "festivals",
        "festival_films",
        "festival_ratings",
        "festival_rating_logs",
        "festival_voice_segments",
        "festival_blocked_films",
        "tarot_draws",
        "tg_channels",
        "santa_events",
        "santa_entries",
        "santa_assignments",
        "voice_snippets",
        "voice_dossier",
        "voice_swear_stats",
        "daily_text_logs",
        "member_profiles",
        "voice_listen_muted",
    ]
    ordered = [t for t in preferred if t in tables] + [
        t for t in tables if t not in preferred and t != "schema_migrations"
    ]

    print(f"Migrating {sqlite_path} → {args.dsn}")
    conn = await asyncpg.connect(args.dsn)
    try:
        for table in ordered:
            n = await copy_table(sqlite_conn, conn, table, force=args.force)
            print(f"  {table}: +{n}")
        await conn.execute(
            "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING",
            33,
        )
    finally:
        await conn.close()
        sqlite_conn.close()
    print("Done. Backup erunda.db, then start the bot on DATABASE_URL.")


if __name__ == "__main__":
    asyncio.run(main())
