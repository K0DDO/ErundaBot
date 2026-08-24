"""Telegram channel directory."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

import discord
from discord import ui

from bot.database.database import Database
from bot.database.models import TgChannel
from bot.utils.embeds import BRAND_COLOR

log = logging.getLogger(__name__)

TG_HOST_RE = re.compile(
    r"^(?:https?://)?(?:t(?:elegram)?\.me|telegram\.dog)/@?([A-Za-z0-9_]{3,})/?$",
    re.IGNORECASE,
)
TG_AT_RE = re.compile(r"^@([A-Za-z0-9_]{3,})$")
OG_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?P<key>og:(?:title|image))["\'][^>]+content=["\'](?P<content>[^"\']+)["\']',
    re.IGNORECASE,
)
OG_META_RE_ALT = re.compile(
    r'<meta[^>]+content=["\'](?P<content>[^"\']+)["\'][^>]+(?:property|name)=["\'](?P<key>og:(?:title|image))["\']',
    re.IGNORECASE,
)
PAGE_TITLE_RE = re.compile(
    r'<div[^>]+class=["\']tgme_page_title["\'][^>]*>\s*<span[^>]*>(?P<title>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class TgChannelMeta:
    title: str
    image_url: str | None = None


class TgkService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def normalize_url(raw: str) -> str:
        value = raw.strip()
        if not value:
            raise ValueError("Ссылка пустая")
        if value.startswith("http://"):
            value = "https://" + value[7:]
        at_match = TG_AT_RE.match(value)
        if at_match:
            return f"https://t.me/{at_match.group(1)}"
        host_match = TG_HOST_RE.match(value)
        if host_match:
            return f"https://t.me/{host_match.group(1)}"
        if value.startswith("https://t.me/"):
            return value.split("?")[0].rstrip("/")
        raise ValueError("Нужна ссылка вида https://t.me/channel или @channel")

    @staticmethod
    def _username(url: str) -> str | None:
        match = TG_HOST_RE.match(url)
        if match is None:
            return None
        name = match.group(1)
        if name.lower() in {"joinchat", "addstickers", "proxy", "socks", "s"}:
            return None
        if name.startswith("+"):
            return None
        return name

    @staticmethod
    def _clean_title(raw: str, username: str) -> str:
        title = html.unescape(re.sub(r"\s+", " ", raw).strip())
        for prefix in ("Telegram: ", "Telegram – ", "Telegram - "):
            if title.startswith(prefix):
                title = title[len(prefix) :].strip()
        if title.lower().startswith("view @"):
            title = title[6:].strip()
        if not title or title.lower() in {"telegram", "telegram messenger"}:
            return f"@{username}"
        return title[:80]

    @staticmethod
    def _normalize_image_url(raw: str) -> str | None:
        image = raw.strip()
        if image.startswith("//"):
            image = "https:" + image
        if image.startswith("https://"):
            return image
        return None

    @classmethod
    def _parse_page_meta(cls, html_text: str, username: str) -> TgChannelMeta:
        og_title: str | None = None
        og_image: str | None = None
        for pattern in (OG_META_RE, OG_META_RE_ALT):
            for match in pattern.finditer(html_text):
                key = match.group("key").lower()
                content = html.unescape(match.group("content").strip())
                if key == "og:title" and og_title is None:
                    og_title = content
                elif key == "og:image" and og_image is None:
                    og_image = cls._normalize_image_url(content)

        title = og_title
        if not title:
            page_match = PAGE_TITLE_RE.search(html_text)
            if page_match:
                title = re.sub(r"<[^>]+>", "", page_match.group("title"))
        cleaned = cls._clean_title(title or f"@{username}", username)
        return TgChannelMeta(title=cleaned, image_url=og_image)

    def fetch_channel_meta(self, url: str) -> TgChannelMeta:
        username = self._username(url)
        if username is None:
            raise ValueError("Нужна публичная ссылка вида https://t.me/channel или @channel")
        request = urllib.request.Request(
            f"https://t.me/{username}",
            headers={"User-Agent": "Mozilla/5.0 ErundaBot"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                page = response.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError("Не удалось открыть страницу канала в Telegram") from exc
        meta = self._parse_page_meta(page, username)
        if meta.title == f"@{username}" and meta.image_url is None:
            raise ValueError("Канал не найден или ссылка ведёт не на публичный ТГК")
        return meta

    async def add(self, guild_id: int, user_id: int, raw_url: str) -> TgChannel:
        url = self.normalize_url(raw_url)
        meta = await asyncio.to_thread(self.fetch_channel_meta, url)
        return await self.db.add_tg_channel(
            guild_id,
            user_id,
            meta.title,
            url,
            meta.image_url,
        )

    async def remove(self, guild_id: int, number: int, user_id: int, *, is_admin: bool) -> TgChannel:
        channel = await self.db.get_tg_channel_by_number(guild_id, number)
        if channel is None:
            raise ValueError("ТГК не найден")
        if channel.user_id != user_id and not is_admin:
            raise ValueError("Можно удалить только свой канал")
        await self.db.delete_tg_channel(channel.id, guild_id)
        await self.db.renumber_tg_channels(guild_id)
        return channel

    async def list_all(self, guild_id: int) -> list[TgChannel]:
        return await self.db.list_tg_channels(guild_id)

    def build_board(self, guild: discord.Guild, channels: list[TgChannel]) -> ui.LayoutView:
        view = ui.LayoutView(timeout=None)
        container = ui.Container(accent_color=BRAND_COLOR)
        container.add_item(ui.TextDisplay("## ТГК участников"))
        if not channels:
            container.add_item(ui.TextDisplay("Пока пусто. `/tgk add` — добавить свой канал."))
            view.add_item(container)
            return view

        grouped: dict[int, list[TgChannel]] = {}
        for channel in channels[:20]:
            grouped.setdefault(channel.user_id, []).append(channel)
        for user_id, items in grouped.items():
            member = guild.get_member(user_id)
            owner = member.display_name if member is not None else f"участник {user_id}"
            container.add_item(ui.TextDisplay(f"**{owner}**"))
            for item in items:
                title = f"# {item.number}  {item.title}"
                link = f"[открыть]({item.url})"
                if item.image_url:
                    container.add_item(
                        ui.Section(
                            ui.TextDisplay(title),
                            ui.TextDisplay(link),
                            accessory=ui.Thumbnail(
                                media=item.image_url,
                                description=item.title[:256],
                            ),
                        )
                    )
                else:
                    container.add_item(ui.TextDisplay(f"{title}\n{link}"))
        view.add_item(container)
        return view

    async def sync_board(
        self,
        guild: discord.Guild,
        bot,
        fallback_channel: discord.abc.Messageable | None = None,
    ) -> discord.Message | None:
        config = await self.db.get_guild(guild.id)
        channel = None
        official = False
        if config is not None and config.tgk_channel_id:
            found = guild.get_channel(config.tgk_channel_id)
            if found is not None and hasattr(found, "send"):
                channel = found
                official = True
        if channel is None:
            channel = fallback_channel
        if channel is None or not hasattr(channel, "send"):
            return None
        channels = await self.list_all(guild.id)
        view = self.build_board(guild, channels)
        message = None
        if official and config is not None and config.tgk_board_message_id:
            try:
                message = await channel.fetch_message(config.tgk_board_message_id)
                await message.edit(content=None, embeds=[], view=view)
            except discord.HTTPException:
                message = None
        if message is None:
            message = await channel.send(view=view)
            if official:
                await self.db.set_tgk_board_message_id(guild.id, message.id)
        return message
