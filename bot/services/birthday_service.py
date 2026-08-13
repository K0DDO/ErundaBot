"""Birthday business logic."""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord

from bot.database.database import Database
from bot.database.models import Birthday, GuildConfig
from bot.utils.embeds import BRAND_COLOR, base_embed
from bot.utils.timezones import parse_hhmm

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

MONTH_GENITIVE = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


@dataclass(slots=True)
class BirthdayEntry:
    birthday: Birthday
    next_date: date
    days_until: int


def format_birthday_date(day: int, month: int, year: int | None = None) -> str:
    base = f"{day} {MONTH_GENITIVE[month]}"
    if year is not None:
        return f"{base} {year}"
    return base


def validate_birthday(day: int, month: int, year: int | None) -> None:
    if not (1 <= month <= 12):
        raise ValueError("Месяц должен быть от 1 до 12")
    if year is not None and year < 1900:
        raise ValueError("Год рождения слишком маленький")
    if year is not None and year > date.today().year:
        raise ValueError("Год рождения не может быть в будущем")
    if year is None:
        max_day = 29 if month == 2 else calendar.monthrange(2024, month)[1]
    else:
        max_day = calendar.monthrange(year, month)[1]
    if not (1 <= day <= max_day):
        raise ValueError(f"Некорректный день для месяца {month}")


def occurrence_on_year(day: int, month: int, year: int) -> date:
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28)
    return date(year, month, day)


def next_occurrence(day: int, month: int, today: date) -> date:
    this_year = occurrence_on_year(day, month, today.year)
    if this_year >= today:
        return this_year
    return occurrence_on_year(day, month, today.year + 1)


def age_on(birthday: Birthday, on_date: date) -> int | None:
    if birthday.year is None:
        return None
    years = on_date.year - birthday.year
    bday_this_year = occurrence_on_year(birthday.day, birthday.month, on_date.year)
    if on_date < bday_this_year:
        years -= 1
    return max(years, 0)


def member_display(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    if member is not None:
        return member.display_name
    return f"участник #{user_id}"


class BirthdayService:
    BOARD_HELP = (
        "**Как добавить свой день рождения:**\n"
        "• `/birthday set` — указать дату\n"
        "• `/birthday remove` — удалить дату"
    )
    MAX_PERSON_EMBEDS = 9  # Discord allows 10 embeds per message (1 header + 9 cards)

    def __init__(self, db: Database) -> None:
        self.db = db

    async def set_birthday(
        self,
        guild_id: int,
        user_id: int,
        day: int,
        month: int,
        year: int | None,
    ) -> Birthday:
        validate_birthday(day, month, year)
        return await self.db.upsert_birthday(guild_id, user_id, day, month, year)

    async def remove_birthday(self, guild_id: int, user_id: int) -> bool:
        return await self.db.remove_birthday(guild_id, user_id)

    async def get_birthday(self, guild_id: int, user_id: int) -> Birthday | None:
        return await self.db.get_birthday(guild_id, user_id)

    async def list_sorted(self, guild_id: int, tz_name: str) -> list[BirthdayEntry]:
        today = datetime.now(ZoneInfo(tz_name)).date()
        entries: list[BirthdayEntry] = []
        for birthday in await self.db.list_birthdays(guild_id):
            nxt = next_occurrence(birthday.day, birthday.month, today)
            entries.append(
                BirthdayEntry(
                    birthday=birthday,
                    next_date=nxt,
                    days_until=(nxt - today).days,
                )
            )
        entries.sort(key=lambda item: (item.days_until, item.birthday.user_id))
        return entries

    async def next_birthday(self, guild_id: int, tz_name: str) -> BirthdayEntry | None:
        entries = await self.list_sorted(guild_id, tz_name)
        return entries[0] if entries else None

    @staticmethod
    def entry_timing(entry: BirthdayEntry) -> str:
        if entry.days_until == 0:
            return "сегодня"
        if entry.days_until == 1:
            return "завтра"
        return f"через {entry.days_until} дн."

    def build_person_embed(self, guild: discord.Guild, entry: BirthdayEntry) -> discord.Embed:
        bday = entry.birthday
        when = format_birthday_date(bday.day, bday.month)
        name = member_display(guild, bday.user_id)
        embed = discord.Embed(
            description=f"{when} ({self.entry_timing(entry)})",
            color=BRAND_COLOR,
        )
        member = guild.get_member(bday.user_id)
        if member is not None:
            embed.set_author(name=name, icon_url=member.display_avatar.url)
        else:
            embed.set_author(name=name)
        return embed

    async def build_board_embeds(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        *,
        person_limit: int | None = None,
    ) -> list[discord.Embed]:
        limit = person_limit if person_limit is not None else self.MAX_PERSON_EMBEDS
        entries = await self.list_sorted(guild.id, config.timezone)
        header = base_embed(
            title="🎂 Дни рождения на сервере",
            description=self.BOARD_HELP,
        )
        if not entries:
            header.description = f"{self.BOARD_HELP}\n\n_Пока никто не указал день рождения._"
            return [header]

        if len(entries) > limit:
            header.description = (
                f"{self.BOARD_HELP}\n\n"
                f"_Показано {limit} из {len(entries)} ближайших._"
            )

        embeds = [header]
        for entry in entries[:limit]:
            embeds.append(self.build_person_embed(guild, entry))
        return embeds

    def format_board_lines(
        self,
        guild: discord.Guild,
        entries: list[BirthdayEntry],
        *,
        limit: int = 30,
    ) -> str:
        if not entries:
            return "_Пока никто не указал день рождения._"
        lines: list[str] = []
        for entry in entries[:limit]:
            bday = entry.birthday
            when = format_birthday_date(bday.day, bday.month)
            name = member_display(guild, bday.user_id)
            if entry.days_until == 0:
                suffix = "сегодня"
            elif entry.days_until == 1:
                suffix = "завтра"
            else:
                suffix = f"через {entry.days_until} дн."
            lines.append(f"• **{name}** — {when} ({suffix})")
        if len(entries) > limit:
            lines.append(f"_…и ещё {len(entries) - limit}_")
        return "\n".join(lines)

    async def sync_board(self, guild: discord.Guild) -> None:
        config = await self.db.get_guild(guild.id)
        if config is None or config.birthday_channel_id is None:
            return
        channel = guild.get_channel(config.birthday_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return

        await self.cleanup_stale_list_messages(guild, channel)

        embeds = await self.build_board_embeds(guild, config)

        if config.birthday_board_message_id:
            try:
                message = await channel.fetch_message(config.birthday_board_message_id)
                await message.edit(content=None, embeds=embeds)
                return
            except discord.NotFound:
                await self.db.set_birthday_board_message_id(guild.id, None)
            except discord.HTTPException:
                log.warning("Failed to edit birthday board in guild %s", guild.id)
                return

        try:
            message = await channel.send(embeds=embeds)
            await self.db.set_birthday_board_message_id(guild.id, message.id)
        except discord.HTTPException:
            log.exception("Failed to post birthday board in guild %s", guild.id)

    async def cleanup_stale_list_messages(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> None:
        """Remove old public /birthday list replies (now ephemeral-only)."""
        if guild.me is None:
            return
        stale_titles = {"Дни рождения", "Ближайший день рождения"}
        try:
            async for message in channel.history(limit=100):
                if message.author.id != guild.me.id or not message.embeds:
                    continue
                title = message.embeds[0].title or ""
                if title in stale_titles:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
        except discord.HTTPException:
            log.warning("Failed to cleanup stale birthday list messages in guild %s", guild.id)

    async def due_announcements(self, config: GuildConfig, now: datetime) -> list[Birthday]:
        if config.birthday_channel_id is None:
            return []

        parsed = parse_hhmm(config.birthday_announce_time)
        if parsed is None:
            return []
        hour, minute = parsed

        local_now = now.astimezone(ZoneInfo(config.timezone))
        if local_now.hour != hour or local_now.minute != minute:
            return []

        today = local_now.date()
        due: list[Birthday] = []
        for birthday in await self.db.list_birthdays(config.guild_id):
            if occurrence_on_year(birthday.day, birthday.month, today.year) != today:
                continue
            event_date = today.isoformat()
            if await self.db.was_birthday_notified(
                config.guild_id, birthday.user_id, event_date, "announce"
            ):
                continue
            due.append(birthday)
        return due

    async def due_reminders(self, config: GuildConfig, now: datetime) -> list[tuple[Birthday, date]]:
        if config.birthday_channel_id is None or config.birthday_reminder_days <= 0:
            return []

        parsed = parse_hhmm(config.birthday_announce_time)
        if parsed is None:
            return []
        hour, minute = parsed

        local_now = now.astimezone(ZoneInfo(config.timezone))
        if local_now.hour != hour or local_now.minute != minute:
            return []

        today = local_now.date()
        target = today + timedelta(days=config.birthday_reminder_days)
        due: list[tuple[Birthday, date]] = []
        for birthday in await self.db.list_birthdays(config.guild_id):
            occ = occurrence_on_year(birthday.day, birthday.month, target.year)
            if occ != target:
                continue
            event_date = occ.isoformat()
            if await self.db.was_birthday_notified(
                config.guild_id, birthday.user_id, event_date, "reminder"
            ):
                continue
            due.append((birthday, occ))
        return due

    async def mark_notified(
        self,
        guild_id: int,
        user_id: int,
        event_date: date,
        kind: str,
    ) -> None:
        await self.db.mark_birthday_notified(
            guild_id,
            user_id,
            event_date.isoformat(),
            kind,
        )
