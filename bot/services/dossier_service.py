"""Daily chat+voice logs → dossier facts; weekly prune of best facts."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord

from bot.services.voice_listen_service import is_dossier_worthy
from bot.utils.gender import Gender, score_gender_markers

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.database import Database
    from bot.services.ai_service import AIService

log = logging.getLogger(__name__)

WEEKLY_KEEP_FACTS = 10
WEEKLY_PRUNE_MIN = 12


class DossierService:
    def __init__(self, db: Database, ai: AIService) -> None:
        self.db = db
        self.ai = ai
        self._daily_done: dict[int, date] = {}
        self._weekly_done: dict[int, str] = {}

    async def record_chat_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        text = (message.content or "").strip()
        if not text:
            return
        config = await self.db.get_guild(message.guild.id)
        tz_name = config.timezone if config is not None else "Europe/Moscow"
        day = datetime.now(ZoneInfo(tz_name)).date().isoformat()
        await self.db.add_daily_text_log(
            message.guild.id,
            message.author.id,
            message.channel.id,
            "chat",
            text[:2000],
            day,
        )
        await self._note_gender(message.guild.id, message.author.id, text)

    async def record_voice_line(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int | None,
        text: str,
        *,
        timezone_name: str = "Europe/Moscow",
    ) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        day = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        await self.db.add_daily_text_log(
            guild_id,
            user_id,
            int(channel_id or 0),
            "voice",
            cleaned[:2000],
            day,
        )
        await self._note_gender(guild_id, user_id, cleaned)

    async def _note_gender(self, guild_id: int, user_id: int, text: str) -> None:
        score_f, score_m = score_gender_markers(text)
        if score_f or score_m:
            await self.db.bump_member_gender_scores(guild_id, user_id, score_f, score_m)

    async def _gender_for(self, guild_id: int, user_id: int) -> Gender:
        value = await self.db.get_member_gender(guild_id, user_id)
        return value if value in {"f", "m", "u"} else "u"

    async def process_daily_and_weekly(self, bot: ErundaBot) -> None:
        try:
            guilds = await self.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for dossier")
            return

        for config in guilds:
            guild = bot.get_guild(config.guild_id)
            if guild is None:
                continue
            local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(config.timezone))
            if local_now.hour != 4 or local_now.minute != 10:
                continue
            if self._daily_done.get(config.guild_id) != local_now.date():
                try:
                    # Process completed local day (yesterday).
                    target = local_now.date() - timedelta(days=1)
                    await self._process_guild_day(guild, target)
                    # Flush any older leftover days.
                    for leftover in await self.db.list_daily_text_log_days(
                        guild.id,
                        before_day=target.isoformat(),
                    ):
                        await self._process_guild_day(
                            guild,
                            date.fromisoformat(leftover),
                        )
                    self._daily_done[config.guild_id] = local_now.date()
                except Exception:
                    log.exception("Daily dossier failed for guild %s", config.guild_id)

            week_key = local_now.date().strftime("%G-W%V")
            # Monday 04:10 — prune to best-of-best.
            if local_now.weekday() == 0 and self._weekly_done.get(config.guild_id) != week_key:
                try:
                    await self._prune_guild_week(guild)
                    self._weekly_done[config.guild_id] = week_key
                except Exception:
                    log.exception("Weekly dossier prune failed for guild %s", config.guild_id)

    async def _process_guild_day(self, guild: discord.Guild, day: date) -> None:
        day_key = day.isoformat()
        user_ids = await self.db.list_daily_text_log_users(guild.id, day_key)
        if not user_ids:
            return
        log.info(
            "Daily dossier guild=%s day=%s users=%s",
            guild.id,
            day_key,
            len(user_ids),
        )
        for user_id in user_ids:
            rows = await self.db.list_daily_text_logs(guild.id, user_id, day_key)
            if not rows:
                continue
            member = guild.get_member(user_id)
            name = member.display_name if member else f"user {user_id}"
            by_channel: dict[int, list[str]] = defaultdict(list)
            for row in rows:
                if not is_dossier_worthy(row.text) and row.source == "voice":
                    continue
                if row.source == "chat" and len(row.text.strip()) < 3:
                    continue
                label = "войс" if row.source == "voice" else "чат"
                by_channel[row.channel_id].append(f"[{label}] {row.text.strip()}")
            if not by_channel:
                await self.db.delete_daily_text_logs(guild.id, user_id, day_key)
                continue
            blocks: list[str] = []
            for channel_id, lines in by_channel.items():
                if channel_id:
                    blocks.append(f"### канал {channel_id}\n" + "\n".join(f"- {x}" for x in lines[:40]))
                else:
                    blocks.append("### без канала\n" + "\n".join(f"- {x}" for x in lines[:40]))
            joined = "\n\n".join(blocks)[:12000]
            # Also learn gender from the day's text before summarizing.
            for row in rows:
                await self._note_gender(guild.id, user_id, row.text)
            gender = await self._gender_for(guild.id, user_id)
            facts = await self.ai.summarize_daily_dossier(
                display_name=name,
                lines=joined,
                gender=gender,
            )
            if facts:
                await self.db.add_voice_dossier_entries(
                    guild.id,
                    user_id,
                    facts[:5],
                    day_key,
                )
            await self.db.delete_daily_text_logs(guild.id, user_id, day_key)

    async def _prune_guild_week(self, guild: discord.Guild) -> None:
        user_ids = await self.db.list_voice_dossier_users(guild.id)
        for user_id in user_ids:
            entries = await self.db.list_voice_dossier(guild.id, user_id, limit=80)
            if len(entries) < WEEKLY_PRUNE_MIN:
                continue
            member = guild.get_member(user_id)
            name = member.display_name if member else f"user {user_id}"
            facts = [item.fact for item in entries]
            gender = await self._gender_for(guild.id, user_id)
            pruned = await self.ai.prune_dossier_facts(
                display_name=name,
                facts=facts,
                keep=WEEKLY_KEEP_FACTS,
                gender=gender,
            )
            if not pruned:
                continue
            await self.db.replace_voice_dossier(
                guild.id,
                user_id,
                pruned,
                source_date=datetime.now(timezone.utc).date().isoformat(),
            )
            log.info(
                "Weekly dossier prune guild=%s user=%s %s→%s",
                guild.id,
                user_id,
                len(facts),
                len(pruned),
            )
