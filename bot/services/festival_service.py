"""Film festival business logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

from bot.database.database import Database
from bot.database.models import Festival, FestivalFilm, GuildConfig
from bot.utils.permissions import fetch_bot_member
from bot.utils.timezones import format_countdown, format_datetime_local, parse_event_datetime

FEST_ROLE_NAME = "Кино"


class FestivalService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_open(self, guild_id: int) -> Festival | None:
        return await self.db.get_open_festival(guild_id)

    async def require_open(self, guild_id: int) -> Festival:
        festival = await self.get_open(guild_id)
        if festival is None:
            raise ValueError("Нет открытого кинофестиваля. Нужен /fest new")
        return festival

    def has_staff(self, member: discord.Member, config: GuildConfig) -> bool:
        if config.fest_staff_role_id is None:
            return False
        return any(role.id == config.fest_staff_role_id for role in member.roles)

    async def require_staff(self, member: discord.Member, config: GuildConfig) -> None:
        if not self.has_staff(member, config):
            raise ValueError("Нужна роль «Кино». Возьми её через /fest role")

    async def ensure_staff_role(self, guild: discord.Guild) -> discord.Role:
        config = await self.db.ensure_guild(guild.id)
        if config.fest_staff_role_id:
            role = guild.get_role(config.fest_staff_role_id)
            if role is not None:
                return role
        bot_member = await fetch_bot_member(guild)
        if not bot_member.guild_permissions.manage_roles:
            raise ValueError("У бота нет права Manage Roles")
        role = await guild.create_role(
            name=FEST_ROLE_NAME,
            mentionable=True,
            reason="Ерунда: роль кинофестиваля",
        )
        await self.db.update_guild(guild.id, fest_staff_role_id=role.id)
        return role

    async def toggle_staff_role(self, guild: discord.Guild, member: discord.Member) -> bool:
        role = await self.ensure_staff_role(guild)
        bot_member = await fetch_bot_member(guild)
        if role >= bot_member.top_role:
            raise ValueError("Роль бота должна быть выше роли «Кино»")
        if role in member.roles:
            await member.remove_roles(role, reason="Ерунда: /fest role")
            return False
        await member.add_roles(role, reason="Ерунда: /fest role")
        return True

    async def create(
        self,
        guild_id: int,
        date_str: str,
        time_str: str,
        tz_name: str,
    ) -> tuple[Festival, Festival | None]:
        starts_at = parse_event_datetime(date_str, time_str, tz_name)
        previous = await self.get_open(guild_id)
        if previous is not None:
            previous = await self.db.update_festival(previous.id, status="closed")
        festival = await self.db.create_festival(guild_id, starts_at.isoformat())
        return festival, previous

    async def set_message(self, festival_id: int, channel_id: int, message_id: int) -> Festival:
        return await self.db.update_festival(
            festival_id,
            channel_id=channel_id,
            message_id=message_id,
        )

    async def add_film(self, guild_id: int, user_id: int, title: str) -> tuple[Festival, FestivalFilm, bool]:
        festival = await self.require_open(guild_id)
        cleaned = title.strip()
        if not cleaned:
            raise ValueError("Название фильма пустое")
        existing = await self.db.get_festival_film(festival.id, user_id)
        film = await self.db.upsert_festival_film(festival.id, user_id, cleaned)
        return festival, film, existing is not None

    async def remove_film(self, guild_id: int, user_id: int) -> Festival:
        festival = await self.require_open(guild_id)
        if not await self.db.remove_festival_film(festival.id, user_id):
            raise ValueError("Ты не предлагал фильм")
        return festival

    async def set_winner(self, guild_id: int, user_id: int) -> tuple[Festival, FestivalFilm]:
        festival = await self.require_open(guild_id)
        film = await self.db.get_festival_film(festival.id, user_id)
        if film is None:
            raise ValueError("У этого человека нет фильма в текущем фестивале")
        festival = await self.db.update_festival(
            festival.id,
            winner_user_id=user_id,
            winner_film=film.title,
            status="closed",
        )
        return festival, film

    async def films(self, festival_id: int) -> list[FestivalFilm]:
        return await self.db.list_festival_films(festival_id)

    def format_starts(self, festival: Festival, tz_name: str) -> tuple[str, str]:
        dt = datetime.fromisoformat(festival.starts_at)
        return format_datetime_local(dt, tz_name)

    def remaining_label(self, festival: Festival, now: datetime | None = None) -> str:
        starts = datetime.fromisoformat(festival.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return format_countdown((starts - current).total_seconds())

    def build_embed(
        self,
        festival: Festival,
        films: list[FestivalFilm],
        tz_name: str,
        guild: discord.Guild | None = None,
    ) -> discord.Embed:
        date_label, time_label = self.format_starts(festival, tz_name)
        lines: list[str] = []
        shown = films[:40]
        for film in shown:
            name = f"<@{film.user_id}>"
            if guild is not None:
                member = guild.get_member(film.user_id)
                if member is not None:
                    name = member.mention
            lines.append(f"{name} — {film.title}")
        extra = len(films) - len(shown)
        if extra > 0:
            lines.append(f"… и ещё {extra}")
        films_text = "\n".join(lines) if lines else "пока никто не предложил"
        if festival.winner_user_id and festival.winner_film:
            winner = f"**Победитель: <@{festival.winner_user_id}> — {festival.winner_film}**"
        else:
            winner = "Победитель: ещё не выбран"
        embed = discord.Embed(
            title=f"🎬 Кинофестиваль #{festival.number}",
            description=(
                f"Сеанс: **{date_label} {time_label}**\n\n"
                f"**Фильмы**\n{films_text}\n\n"
                f"{winner}"
            ),
            color=0x7C9CFF if festival.status == "open" else 0x57F287,
        )
        embed.set_footer(text="Ерунда")
        return embed

    def export_names(self, films: list[FestivalFilm], guild: discord.Guild) -> str:
        names: list[str] = []
        for film in films:
            member = guild.get_member(film.user_id)
            names.append(member.display_name if member is not None else f"участник {film.user_id}")
        return "\n".join(names) if names else "пока нет заявок"

    def ping_text(self, festival: Festival, tz_name: str, role: discord.Role) -> str:
        date_label, time_label = self.format_starts(festival, tz_name)
        left = self.remaining_label(festival)
        return (
            f"{role.mention} до фильма **{left}** "
            f"(сеанс {date_label} в {time_label})."
        )

    async def due_reminders(self, config: GuildConfig, now: datetime) -> list[Festival]:
        if config.fest_channel_id is None or config.fest_reminder_minutes <= 0:
            return []
        if config.fest_ping_role_id is None:
            return []
        due: list[Festival] = []
        for festival in await self.db.list_open_festivals():
            if festival.guild_id != config.guild_id or festival.reminder_sent:
                continue
            starts = datetime.fromisoformat(festival.starts_at)
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=timezone.utc)
            if now >= starts:
                continue
            if starts - now <= timedelta(minutes=config.fest_reminder_minutes):
                due.append(festival)
        return due
