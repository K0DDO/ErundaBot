"""Film festival business logic."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import discord

from bot.database.database import Database
from bot.database.models import Festival, FestivalFilm, GuildConfig
from bot.utils.permissions import fetch_bot_member
from bot.utils.timezones import format_countdown, format_datetime_local, parse_event_datetime

FEST_ROLE_NAME = "Кино"
POSTER_USER_AGENT = "ErundaBot/1.0 (https://github.com/K0DDO/ErundaBot)"


def normalize_film_title(title: str) -> str:
    cleaned = " ".join(title.split())
    words: list[str] = []
    for word in cleaned.split(" "):
        bits = word.split("-")
        words.append("-".join(bit[:1].upper() + bit[1:] if bit else bit for bit in bits))
    return " ".join(words)


def _http_json(url: str, timeout: int = 8) -> dict | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": POSTER_USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _wikipedia_poster(title: str) -> str | None:
    queries = (
        ("ru", f"{title} фильм"),
        ("en", f"{title} film"),
    )
    for lang, query in queries:
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 5,
                    "format": "json",
                }
            )
        )
        data = _http_json(search_url)
        hits = ((data or {}).get("query") or {}).get("search") or []
        for hit in hits:
            page_id = hit.get("pageid")
            if not page_id:
                continue
            image_url = (
                f"https://{lang}.wikipedia.org/w/api.php?"
                + urllib.parse.urlencode(
                    {
                        "action": "query",
                        "pageids": page_id,
                        "prop": "pageimages",
                        "pithumbsize": 500,
                        "pilicense": "any",
                        "format": "json",
                    }
                )
            )
            page_data = _http_json(image_url)
            pages = ((page_data or {}).get("query") or {}).get("pages") or {}
            page = pages.get(str(page_id)) or {}
            source = ((page.get("thumbnail") or {}).get("source")) if isinstance(page, dict) else None
            if isinstance(source, str) and source.startswith("https://"):
                return source
    return None


def _itunes_poster(title: str) -> str | None:
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {
            "term": title,
            "entity": "movie",
            "country": "ru",
            "limit": 3,
        }
    )
    data = _http_json(url)
    for item in (data or {}).get("results") or []:
        art = item.get("artworkUrl100") or item.get("artworkUrl60")
        if not isinstance(art, str) or not art.startswith("https://"):
            continue
        return (
            art.replace("100x100bb", "600x600bb")
            .replace("60x60bb", "600x600bb")
        )
    return None


def fetch_film_poster(title: str) -> str | None:
    return _wikipedia_poster(title) or _itunes_poster(title)


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
        cleaned = normalize_film_title(title)
        if not cleaned:
            raise ValueError("Название фильма пустое")
        existing = await self.db.get_festival_film(festival.id, user_id)
        image_url = await asyncio.to_thread(fetch_film_poster, cleaned)
        film = await self.db.upsert_festival_film(festival.id, user_id, cleaned, image_url)
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
            winner_film=normalize_film_title(film.title),
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
            lines.append(f"{name} — {normalize_film_title(film.title)}")
        extra = len(films) - len(shown)
        if extra > 0:
            lines.append(f"… и ещё {extra}")
        films_text = "\n".join(lines) if lines else "пока никто не предложил"
        if festival.winner_user_id and festival.winner_film:
            winner = f"**Победитель: <@{festival.winner_user_id}> — {normalize_film_title(festival.winner_film)}**"
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

    def poster_urls(self, festival: Festival, films: list[FestivalFilm]) -> list[tuple[str, str]]:
        ordered: list[FestivalFilm] = []
        if festival.winner_user_id:
            for film in films:
                if film.user_id == festival.winner_user_id and film.image_url:
                    ordered.append(film)
                    break
        for film in films:
            if film.image_url and film not in ordered:
                ordered.append(film)
        result: list[tuple[str, str]] = []
        for film in ordered[:10]:
            if film.image_url:
                result.append((film.image_url, normalize_film_title(film.title)))
        return result

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
