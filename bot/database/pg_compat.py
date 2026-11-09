"""asyncpg connection shim that accepts aiosqlite-style SQL (`?`, datetime('now'))."""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

import asyncpg

log = logging.getLogger(__name__)

_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_DATETIME_REL = re.compile(r"datetime\(\s*'now'\s*,\s*\?\s*\)", re.IGNORECASE)
_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_INSERT_OR_REPLACE = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE)
_AUTOINCREMENT = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
    re.IGNORECASE,
)
_RETURNING_TABLES = re.compile(
    r"INSERT\s+INTO\s+(events|quotes|festivals|tg_channels|santa_events|"
    r"voice_sessions|festival_rating_logs|festival_voice_segments|"
    r"custom_roles|proposals|voice_snippets|voice_dossier|daily_text_logs)\b",
    re.IGNORECASE,
)


def adapt_sql(sql: str) -> str:
    text = sql.strip()
    # datetime('now', ?) → (NOW() + ($n)::interval); first ? consumed here.
    rel_slots: list[str] = []

    def rel_repl(_m: re.Match[str]) -> str:
        rel_slots.append("__REL__")
        return "(NOW() + (__REL__)::interval)"

    text = _DATETIME_REL.sub(rel_repl, text)
    text = _DATETIME_NOW.sub("NOW()", text)
    text = _INSERT_OR_IGNORE.sub("INSERT INTO", text)
    text = _INSERT_OR_REPLACE.sub("INSERT INTO", text)

    n = 0

    def qmark(_m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return f"${n}"

    # Replace __REL__ markers with numbered placeholders in order of appearance,
    # then remaining ?.
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith("__REL__", i):
            n += 1
            out.append(f"${n}")
            i += len("__REL__")
            continue
        if text[i] == "?":
            n += 1
            out.append(f"${n}")
            i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def adapt_schema_sql(sql: str) -> str:
    text = sql.strip()
    if re.match(r"^PRAGMA\b", text, flags=re.IGNORECASE):
        return ""
    text = re.sub(r"PRAGMA\s+[^;]+;?", "", text, flags=re.IGNORECASE)
    text = _DATETIME_NOW.sub("NOW()", text)
    text = _AUTOINCREMENT.sub("BIGSERIAL PRIMARY KEY", text)
    # Discord snowflakes exceed int32 — use BIGINT for all integer columns.
    text = re.sub(r"\bINTEGER\b", "BIGINT", text, flags=re.IGNORECASE)
    return text


class CompatCursor:
    def __init__(
        self,
        *,
        rows: list[asyncpg.Record] | None = None,
        rowcount: int = -1,
        lastrowid: int | None = None,
    ) -> None:
        self._rows = rows or []
        self._index = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    async def fetchone(self) -> asyncpg.Record | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    async def fetchall(self) -> list[asyncpg.Record]:
        remaining = self._rows[self._index :]
        self._index = len(self._rows)
        return remaining


class CompatConnection:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def execute(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
    ) -> CompatCursor:
        params = tuple(params or ())
        adapted = adapt_sql(sql).rstrip().rstrip(";")
        upper = sql.upper()
        conflict_sql = ""
        if "INSERT OR IGNORE" in upper and "ON CONFLICT" not in adapted.upper():
            conflict_sql = " ON CONFLICT DO NOTHING"
        elif "INSERT OR REPLACE" in upper and "ON CONFLICT" not in adapted.upper():
            if "tarot_draws" in sql:
                conflict_sql = (
                    " ON CONFLICT (guild_id, user_id, card_id) DO UPDATE SET "
                    "reversed = EXCLUDED.reversed, drawn_at = EXCLUDED.drawn_at"
                )

        returning_sql = ""
        is_insert = adapted.lstrip().upper().startswith("INSERT")
        if (
            is_insert
            and "RETURNING" not in adapted.upper()
            and _RETURNING_TABLES.search(adapted)
        ):
            returning_sql = " RETURNING id"
        adapted = adapted + conflict_sql + returning_sql

        async with self._pool.acquire() as conn:
            if adapted.lstrip().upper().startswith("SELECT") or " RETURNING " in adapted.upper():
                rows = await conn.fetch(adapted, *params)
                lastrowid = None
                if rows and "id" in rows[0].keys():
                    try:
                        lastrowid = int(rows[0]["id"])
                    except (TypeError, ValueError, KeyError):
                        lastrowid = None
                return CompatCursor(rows=list(rows), rowcount=len(rows), lastrowid=lastrowid)

            status = await conn.execute(adapted, *params)
            rowcount = -1
            parts = status.split()
            if parts and parts[-1].isdigit():
                rowcount = int(parts[-1])
            return CompatCursor(rows=[], rowcount=rowcount, lastrowid=None)

    async def executescript(self, script: str) -> None:
        chunks = [c.strip() for c in script.split(";") if c.strip()]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for chunk in chunks:
                    sql = adapt_schema_sql(chunk)
                    if not sql.strip():
                        continue
                    try:
                        await conn.execute(sql)
                    except asyncpg.DuplicateTableError:
                        pass
                    except asyncpg.DuplicateObjectError:
                        pass

    async def commit(self) -> None:
        return None

    async def close(self) -> None:
        await self._pool.close()
