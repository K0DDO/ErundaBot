"""Track festival voice presence for rating eligibility."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord

from bot.database.database import Database
from bot.database.models import Festival

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)

FEST_PRESENCE_FROM_NUMBER = 33
FEST_PRESENCE_MIN_SECONDS = 15 * 60
FEST_PRESENCE_GRACE_MINUTES = 10
FEST_PRESENCE_MIN_HUMANS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class FestivalPresenceService:
    def __init__(self, db: Database, festival_service) -> None:
        self.db = db
        self.festival_service = festival_service

    def applies_to(self, festival: Festival) -> bool:
        return festival.number >= FEST_PRESENCE_FROM_NUMBER and bool(festival.winner_user_id)

    def presence_bounds(
        self,
        festival: Festival,
        runtime_minutes: int | None,
    ) -> tuple[datetime, datetime]:
        starts, ends = self.festival_service.session_bounds(festival, runtime_minutes)
        return starts, ends + timedelta(minutes=FEST_PRESENCE_GRACE_MINUTES)

    def in_presence_window(
        self,
        festival: Festival,
        runtime_minutes: int | None,
        now: datetime | None = None,
    ) -> bool:
        current = now or _utc_now()
        starts, ends = self.presence_bounds(festival, runtime_minutes)
        return starts <= current < ends

    async def _runtime_for(self, festival: Festival) -> int | None:
        if not festival.winner_user_id:
            return None
        film = await self.db.get_festival_film(festival.id, festival.winner_user_id)
        return film.runtime_minutes if film is not None else None

    @staticmethod
    def channel_eligible_user_ids(channel: discord.VoiceChannel) -> set[int]:
        humans = [member for member in channel.members if not member.bot]
        if len(humans) < FEST_PRESENCE_MIN_HUMANS:
            return set()
        streaming = any(
            member.voice is not None and member.voice.self_stream for member in humans
        )
        if not streaming:
            return set()
        return {member.id for member in humans}

    async def recover(self, bot: ErundaBot) -> None:
        now = _iso(_utc_now())
        await self.db.close_all_open_festival_voice_segments(now)
        for guild in bot.guilds:
            try:
                await self.sync_guild(guild)
            except Exception:
                log.exception("Festival presence recover failed for guild %s", guild.id)

    async def sync_guild(self, guild: discord.Guild) -> None:
        festivals = await self.db.list_guild_festivals(guild.id)
        active: list[Festival] = []
        now = _utc_now()
        now_iso = _iso(now)
        for festival in festivals:
            if not self.applies_to(festival):
                continue
            runtime = await self._runtime_for(festival)
            if self.in_presence_window(festival, runtime, now):
                active.append(festival)
            else:
                await self.db.close_festival_voice_segments(festival.id, now_iso)

        if not active:
            return

        eligible_by_fest: dict[int, set[int]] = {festival.id: set() for festival in active}
        channel_by_user: dict[tuple[int, int], int] = {}
        for channel in guild.voice_channels:
            eligible = self.channel_eligible_user_ids(channel)
            if not eligible:
                continue
            for festival in active:
                for user_id in eligible:
                    eligible_by_fest[festival.id].add(user_id)
                    channel_by_user[(festival.id, user_id)] = channel.id

        for festival in active:
            await self._sync_festival_segments(
                festival.id,
                eligible_by_fest[festival.id],
                channel_by_user,
                now_iso,
            )

    async def _sync_festival_segments(
        self,
        festival_id: int,
        eligible: set[int],
        channel_by_user: dict[tuple[int, int], int],
        now_iso: str,
    ) -> None:
        open_rows = await self.db.list_open_festival_voice_segments(festival_id)
        open_by_user = {int(row["user_id"]): row for row in open_rows}

        for user_id, row in list(open_by_user.items()):
            wanted = channel_by_user.get((festival_id, user_id)) if user_id in eligible else None
            if user_id not in eligible or wanted is None:
                await self.db.close_festival_voice_segment(int(row["id"]), now_iso)
                open_by_user.pop(user_id, None)
                continue
            if int(row["channel_id"]) != wanted:
                await self.db.close_festival_voice_segment(int(row["id"]), now_iso)
                open_by_user.pop(user_id, None)

        for user_id in eligible:
            if user_id in open_by_user:
                continue
            channel_id = channel_by_user.get((festival_id, user_id))
            if channel_id is None:
                continue
            await self.db.open_festival_voice_segment(
                festival_id,
                user_id,
                channel_id,
                now_iso,
            )

    async def user_seconds(self, festival_id: int, user_id: int) -> int:
        return await self.db.festival_voice_user_seconds(
            festival_id,
            user_id,
            _iso(_utc_now()),
        )

    async def ensure_can_rate(
        self,
        festival: Festival,
        user_id: int,
        runtime_minutes: int | None,
        *,
        presence_check_enabled: bool,
    ) -> None:
        if not presence_check_enabled or festival.number < FEST_PRESENCE_FROM_NUMBER:
            return
        seconds = await self.user_seconds(festival.id, user_id)
        if seconds >= FEST_PRESENCE_MIN_SECONDS:
            return
        if self.in_presence_window(festival, runtime_minutes):
            left = FEST_PRESENCE_MIN_SECONDS - seconds
            mins = max(1, (left + 59) // 60)
            raise ValueError(
                f"Пока оценить нельзя — смотри фильм дальше (ещё ~{mins} мин. в войсе)"
            )
        raise ValueError("Не можете оценить фильм: вы не присутствовали на нём")
