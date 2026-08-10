"""Activity statistics: messages, voice, reactions, tops."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from bot.database.database import Database


class StatPeriod(str, Enum):
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    ALL = "all"


class StatCategory(str, Enum):
    MESSAGES = "messages"
    VOICE = "voice"
    REACTIONS = "reactions"
    OVERALL = "overall"


PERIOD_LABELS = {
    StatPeriod.TODAY: "сегодня",
    StatPeriod.WEEK: "эта неделя",
    StatPeriod.MONTH: "этот месяц",
    StatPeriod.ALL: "всё время",
}

CATEGORY_LABELS = {
    StatCategory.MESSAGES: "сообщения",
    StatCategory.VOICE: "voice",
    StatCategory.REACTIONS: "реакции",
    StatCategory.OVERALL: "общая активность",
}

MONTH_NAMES_RU = (
    "",
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


@dataclass(slots=True)
class UserStats:
    messages: int
    voice_seconds: int
    reactions: int
    message_rank: int | None
    voice_rank: int | None
    reaction_rank: int | None


@dataclass(slots=True)
class TopEntry:
    user_id: int
    value: int


def overall_score(messages: int, voice_seconds: int, reactions: int) -> int:
    """Weighted activity: 1 msg + 1 per voice-minute + 1 reaction."""
    return messages + (voice_seconds // 60) + reactions


class StatisticsService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _tz(self, tz_name: str) -> ZoneInfo:
        return ZoneInfo(tz_name)

    def local_now(self, tz_name: str) -> datetime:
        return datetime.now(self._tz(tz_name))

    def local_today(self, tz_name: str) -> date:
        return self.local_now(tz_name).date()

    def period_bounds(
        self,
        period: StatPeriod,
        tz_name: str,
    ) -> tuple[date | None, date | None, datetime | None, datetime | None]:
        """Return (date_from, date_to, datetime_from, datetime_to) in guild tz."""
        now = self.local_now(tz_name)
        today = now.date()
        if period == StatPeriod.ALL:
            return None, None, None, now
        if period == StatPeriod.TODAY:
            start = datetime.combine(today, time.min, tzinfo=self._tz(tz_name))
            return today, today, start, now
        if period == StatPeriod.WEEK:
            start_date = today - timedelta(days=today.weekday())
            start = datetime.combine(start_date, time.min, tzinfo=self._tz(tz_name))
            return start_date, today, start, now
        # month
        start_date = today.replace(day=1)
        start = datetime.combine(start_date, time.min, tzinfo=self._tz(tz_name))
        return start_date, today, start, now

    def period_title(self, period: StatPeriod, tz_name: str) -> str:
        today = self.local_today(tz_name)
        if period == StatPeriod.TODAY:
            return "сегодня"
        if period == StatPeriod.WEEK:
            return "эта неделя"
        if period == StatPeriod.MONTH:
            return MONTH_NAMES_RU[today.month]
        return "всё время"

    async def record_message(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        tz_name: str,
    ) -> None:
        day = self.local_today(tz_name).isoformat()
        await self.db.increment_messages(guild_id, user_id, channel_id, day)

    async def record_reaction(
        self,
        guild_id: int,
        author_id: int,
        tz_name: str,
        amount: int = 1,
    ) -> None:
        day = self.local_today(tz_name).isoformat()
        await self.db.increment_reactions(guild_id, author_id, day, amount)

    def _iso(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    async def start_voice(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        tz_name: str,
    ) -> None:
        started = self.local_now(tz_name).isoformat()
        await self.db.open_voice_session(guild_id, user_id, channel_id, started)

    async def end_voice(self, guild_id: int, user_id: int, tz_name: str) -> None:
        ended = self.local_now(tz_name).isoformat()
        await self.db.close_voice_session(guild_id, user_id, ended)

    async def recover_voice_sessions(self, bot) -> None:
        """Close orphan sessions, then reopen for members currently in non-AFK voice."""
        now_utc = datetime.now().astimezone().isoformat()
        await self.db.close_all_open_voice_sessions(now_utc)

        for guild in bot.guilds:
            config = await self.db.ensure_guild(guild.id)
            afk_id = guild.afk_channel.id if guild.afk_channel else None
            for channel in guild.voice_channels:
                if afk_id is not None and channel.id == afk_id:
                    continue
                for member in channel.members:
                    if member.bot:
                        continue
                    await self.start_voice(
                        guild.id,
                        member.id,
                        channel.id,
                        config.timezone,
                    )

    async def get_user_stats(self, guild_id: int, user_id: int, tz_name: str) -> UserStats:
        messages = await self.db.sum_messages(guild_id, user_id, None, None)
        reactions = await self.db.sum_reactions(guild_id, user_id, None, None)
        now_iso = self.local_now(tz_name).isoformat()
        voice = await self.db.sum_voice_seconds(guild_id, user_id, None, None, now_iso)
        assert isinstance(messages, int)
        assert isinstance(reactions, int)
        assert isinstance(voice, int)

        msg_top = await self.db.sum_messages(guild_id, None, None, None)
        voice_top = await self.db.sum_voice_seconds(guild_id, None, None, None, now_iso)
        react_top = await self.db.sum_reactions(guild_id, None, None, None)
        assert isinstance(msg_top, list)
        assert isinstance(voice_top, list)
        assert isinstance(react_top, list)

        return UserStats(
            messages=messages,
            voice_seconds=voice,
            reactions=reactions,
            message_rank=_rank_of(msg_top, user_id),
            voice_rank=_rank_of(voice_top, user_id),
            reaction_rank=_rank_of(react_top, user_id),
        )

    async def get_top(
        self,
        guild_id: int,
        category: StatCategory,
        period: StatPeriod,
        tz_name: str,
        limit: int = 10,
    ) -> list[TopEntry]:
        date_from, date_to, dt_from, dt_to = self.period_bounds(period, tz_name)
        date_from_s = date_from.isoformat() if date_from else None
        date_to_s = date_to.isoformat() if date_to else None
        now_iso = (dt_to or self.local_now(tz_name)).isoformat()
        start_iso = self._iso(dt_from)

        if category == StatCategory.MESSAGES:
            rows = await self.db.sum_messages(guild_id, None, date_from_s, date_to_s)
            assert isinstance(rows, list)
            return [TopEntry(uid, val) for uid, val in rows[:limit]]

        if category == StatCategory.REACTIONS:
            rows = await self.db.sum_reactions(guild_id, None, date_from_s, date_to_s)
            assert isinstance(rows, list)
            return [TopEntry(uid, val) for uid, val in rows[:limit]]

        if category == StatCategory.VOICE:
            rows = await self.db.sum_voice_seconds(
                guild_id, None, start_iso, now_iso, now_iso
            )
            assert isinstance(rows, list)
            return [TopEntry(uid, val) for uid, val in rows[:limit]]

        # overall
        msg_rows = await self.db.sum_messages(guild_id, None, date_from_s, date_to_s)
        react_rows = await self.db.sum_reactions(guild_id, None, date_from_s, date_to_s)
        voice_rows = await self.db.sum_voice_seconds(
            guild_id, None, start_iso, now_iso, now_iso
        )
        assert isinstance(msg_rows, list)
        assert isinstance(react_rows, list)
        assert isinstance(voice_rows, list)
        scores: dict[int, int] = {}
        for uid, val in msg_rows:
            scores[uid] = scores.get(uid, 0) + val
        for uid, val in react_rows:
            scores[uid] = scores.get(uid, 0) + val
        for uid, val in voice_rows:
            scores[uid] = scores.get(uid, 0) + (val // 60)
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [TopEntry(uid, val) for uid, val in ordered[:limit]]


def _rank_of(rows: list[tuple[int, int]], user_id: int) -> int | None:
    for index, (uid, value) in enumerate(rows, start=1):
        if uid == user_id:
            return index if value > 0 else None
    return None
