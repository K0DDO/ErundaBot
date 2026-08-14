"""Film festival business logic."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord

from bot.database.database import Database
from bot.database.models import Festival, FestivalFilm, GuildConfig
from bot.utils.permissions import fetch_bot_member
from bot.utils.timezones import format_countdown, format_datetime_local, parse_event_datetime

FEST_ROLE_NAME = "Кино"
POSTER_USER_AGENT = "ErundaBot/1.0 (https://github.com/K0DDO/ErundaBot)"
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


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


def _http_html(url: str, timeout: int = 8) -> str | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": POSTER_USER_AGENT, "Accept": "text/html"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _og_image_from_html(html: str) -> str | None:
    match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE_ALT.search(html)
    if match is None:
        return None
    image = match.group(1).strip()
    if image.startswith("//"):
        image = "https:" + image
    if image.startswith("https://"):
        return image
    return None


def _kinopoisk_poster(title: str) -> str | None:
    url = "https://www.kinopoisk.ru/index.php?" + urllib.parse.urlencode({"kp_query": title})
    html = _http_html(url)
    if not html:
        return None
    return _og_image_from_html(html)


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
    return _kinopoisk_poster(title) or _wikipedia_poster(title) or _itunes_poster(title)


def pick_guild_emoji(guild: discord.Guild | None, seed: int) -> str:
    if guild is None:
        return "🎬"
    extra = getattr(guild, "_erunda_emojis", None)
    pool = list(extra) if extra else list(getattr(guild, "emojis", ()) or ())
    usable = [emoji for emoji in pool if getattr(emoji, "available", True)]
    if not usable:
        return "🎬"
    return str(usable[seed % len(usable)])


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

    async def update_starts(
        self,
        guild_id: int,
        date_str: str,
        time_str: str,
        tz_name: str,
    ) -> Festival:
        festival = await self.require_open(guild_id)
        starts_at = parse_event_datetime(date_str, time_str, tz_name)
        return await self.db.update_festival(
            festival.id,
            starts_at=starts_at.isoformat(),
            reminder_sent=0,
        )

    async def delete(self, guild_id: int) -> Festival:
        festival = await self.require_open(guild_id)
        await self.db.delete_festival(festival.id)
        return festival

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

    async def ensure_posters(self, festival_id: int) -> list[FestivalFilm]:
        films = await self.films(festival_id)
        result: list[FestivalFilm] = []
        for film in films:
            if film.image_url:
                result.append(film)
                continue
            image_url = await asyncio.to_thread(fetch_film_poster, film.title)
            if image_url:
                film = await self.db.upsert_festival_film(
                    festival_id, film.user_id, film.title, image_url
                )
            result.append(film)
        return result

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

    def starts_input(self, festival: Festival, tz_name: str) -> tuple[str, str]:
        dt = datetime.fromisoformat(festival.starts_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(ZoneInfo(tz_name))
        return local.strftime("%d.%m.%Y"), local.strftime("%H:%M")

    def remaining_label(self, festival: Festival, now: datetime | None = None) -> str:
        starts = datetime.fromisoformat(festival.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return format_countdown((starts - current).total_seconds())

    def _mention(self, user_id: int, guild: discord.Guild | None) -> str:
        name = f"<@{user_id}>"
        if guild is not None:
            member = guild.get_member(user_id)
            if member is not None:
                name = member.mention
        return name

    def card_body(
        self,
        festival: Festival,
        films: list[FestivalFilm],
        tz_name: str,
        guild: discord.Guild | None = None,
        *,
        winner_emoji: str = "🎬",
        include_films: bool = True,
    ) -> str:
        date_label, time_label = self.format_starts(festival, tz_name)
        parts = [f"Сеанс: **{date_label} {time_label}**"]
        if include_films:
            shown = films[:40]
            lines = [
                f"{self._mention(film.user_id, guild)} — {normalize_film_title(film.title)}"
                for film in shown
            ]
            extra = len(films) - len(shown)
            if extra > 0:
                lines.append(f"… и ещё {extra}")
            films_text = "\n".join(lines) if lines else "пока никто не предложил"
            parts.append(f"**Фильмы**\n{films_text}")
        if festival.winner_user_id and festival.winner_film:
            parts.append(
                "**Победитель**\n"
                f"{self._mention(festival.winner_user_id, guild)}\n"
                f"### {winner_emoji} {normalize_film_title(festival.winner_film)}"
            )
        else:
            parts.append("Победитель: ещё не выбран")
        return "\n\n".join(parts)

    def poster_urls(self, festival: Festival, films: list[FestivalFilm]) -> list[tuple[str, str]]:
        chosen: list[FestivalFilm]
        if festival.winner_user_id:
            chosen = [
                film
                for film in films
                if film.user_id == festival.winner_user_id and film.image_url
            ][:1]
        else:
            chosen = [film for film in films if film.image_url][:10]
        return [
            (film.image_url, normalize_film_title(film.title))
            for film in chosen
            if film.image_url
        ]

    def export_names(self, films: list[FestivalFilm], guild: discord.Guild) -> str:
        names: list[str] = []
        for film in films:
            member = guild.get_member(film.user_id)
            names.append(member.display_name if member is not None else f"участник {film.user_id}")
        return "\n".join(names) if names else "пока нет заявок"

    def ping_text(self, festival: Festival, tz_name: str, role: discord.Role) -> str:
        starts = datetime.fromisoformat(festival.starts_at)
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= starts:
            return f"{role.mention} мы уже смотрим фильм."
        left = self.remaining_label(festival)
        return f"{role.mention} до фильма **{left}**."

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
