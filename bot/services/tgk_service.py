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
from bot.utils.birthday_emojis import CUSTOM_EMOJI_SEARCH_RE, render_birthday_emoji
from bot.utils.embeds import BRAND_COLOR

log = logging.getLogger(__name__)

TG_HOST_RE = re.compile(
    r"^(?:https?://)?(?:t(?:elegram)?\.me|telegram\.dog)/@?([A-Za-z0-9_]{3,})/?$",
    re.IGNORECASE,
)
TG_INVITE_RE = re.compile(
    r"^(?:https?://)?(?:t(?:elegram)?\.me|telegram\.dog)/(\+[A-Za-z0-9_-]+)/?$",
    re.IGNORECASE,
)
TG_JOINCHAT_RE = re.compile(
    r"^(?:https?://)?(?:t(?:elegram)?\.me|telegram\.dog)/joinchat/([A-Za-z0-9_-]+)/?$",
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
    DISCORD_COMPONENT_LIMIT = 40
    MAX_BOARD_CHANNELS = 10
    OWNER_SEPARATOR_WIDTH = 52

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
        invite_match = TG_INVITE_RE.match(value)
        if invite_match:
            return f"https://t.me/{invite_match.group(1)}"
        joinchat_match = TG_JOINCHAT_RE.match(value)
        if joinchat_match:
            return f"https://t.me/joinchat/{joinchat_match.group(1)}"
        host_match = TG_HOST_RE.match(value)
        if host_match:
            return f"https://t.me/{host_match.group(1)}"
        if value.startswith("https://t.me/"):
            return value.split("?")[0].rstrip("/")
        raise ValueError(
            "Нужна ссылка вида https://t.me/channel, @channel или https://t.me/+invite"
        )

    @staticmethod
    def _public_username(url: str) -> str | None:
        match = TG_HOST_RE.match(url)
        if match is None:
            return None
        name = match.group(1)
        if name.lower() in {"joinchat", "addstickers", "proxy", "socks", "s"}:
            return None
        return name

    @staticmethod
    def _page_label(url: str) -> str:
        username = TgkService._public_username(url)
        if username is not None:
            return f"@{username}"
        if TG_INVITE_RE.match(url) or TG_JOINCHAT_RE.match(url):
            return "Telegram-канал"
        return "канал"

    @staticmethod
    def _clean_title(raw: str, fallback: str) -> str:
        title = html.unescape(re.sub(r"\s+", " ", raw).strip())
        for prefix in ("Telegram: ", "Telegram – ", "Telegram - "):
            if title.startswith(prefix):
                title = title[len(prefix) :].strip()
        if title.lower().startswith("view @"):
            title = title[6:].strip()
        if not title or title.lower() in {"telegram", "telegram messenger"}:
            return fallback[:80]
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
    def _parse_page_meta(cls, html_text: str, fallback: str) -> TgChannelMeta:
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
        cleaned = cls._clean_title(title or fallback, fallback)
        return TgChannelMeta(title=cleaned, image_url=og_image)

    def fetch_channel_meta(self, url: str) -> TgChannelMeta:
        normalized = self.normalize_url(url)
        fallback = self._page_label(normalized)
        request = urllib.request.Request(
            normalized,
            headers={"User-Agent": "Mozilla/5.0 ErundaBot"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                page = response.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError("Не удалось открыть страницу канала в Telegram") from exc
        meta = self._parse_page_meta(page, fallback)
        if meta.title == fallback and meta.image_url is None:
            raise ValueError("Канал не найден или ссылка ведёт не на Telegram-канал")
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

    @staticmethod
    def is_private_url(url: str) -> bool:
        if TG_INVITE_RE.match(url) or TG_JOINCHAT_RE.match(url):
            return True
        lowered = url.lower()
        return "/+" in lowered or "/joinchat/" in lowered

    @staticmethod
    def _visual_len(text: str) -> int:
        """Approximate monospace width for centering (emoji ≈ 2, custom emoji = 1)."""
        normalized = CUSTOM_EMOJI_SEARCH_RE.sub("E", text)
        size = 0
        idx = 0
        while idx < len(normalized):
            ch = normalized[idx]
            code = ord(ch)
            if code == 0xFE0F:
                idx += 1
                continue
            if code > 0xFFFF:
                size += 2
                idx += 2
                continue
            if 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF:
                size += 2
                idx += 1
                if idx < len(normalized) and ord(normalized[idx]) == 0xFE0F:
                    idx += 1
                continue
            size += 1
            idx += 1
        return size

    @classmethod
    def _centered_dashes(cls, center: str, width: int | None = None) -> str:
        line_width = width if width is not None else cls.OWNER_SEPARATOR_WIDTH
        center_len = cls._visual_len(center)
        if center_len >= line_width:
            return center
        pad = line_width - center_len
        left = pad // 2
        right = pad - left
        return f"{'-' * left}{center}{'-' * right}"

    @staticmethod
    def _owner_separator(guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        username = member.name if member is not None else f"user{user_id}"
        emoji = render_birthday_emoji(guild, None, user_id=user_id)
        center = f" {emoji} {username} "
        return TgkService._centered_dashes(center)

    @staticmethod
    def _channel_title(display_number: int, item: TgChannel) -> str:
        return f"# {display_number}.  {item.title}"

    @staticmethod
    def _channel_link(item: TgChannel) -> str:
        kind = "приватка" if TgkService.is_private_url(item.url) else "открытый"
        return f"[открыть]({item.url})\n-# {kind}"

    @staticmethod
    def board_order(channels: list[TgChannel]) -> list[TgChannel]:
        return sorted(channels, key=lambda c: (c.number, c.id))

    @staticmethod
    def estimate_component_count(channel_count: int, owner_count: int) -> int:
        if channel_count <= 0:
            return 2
        return 2 + owner_count * 3 + channel_count * 3

    @classmethod
    def max_channels_for_owners(cls, owner_count: int) -> int:
        if owner_count <= 0:
            return cls.MAX_BOARD_CHANNELS
        budget = cls.DISCORD_COMPONENT_LIMIT - 2 - owner_count * 3
        if budget < 3:
            return 0
        return min(cls.MAX_BOARD_CHANNELS, budget // 3)

    @classmethod
    def board_capacity_hint(cls) -> str:
        return (
            f"В одно сообщение помещается примерно {cls.MAX_BOARD_CHANNELS} ТГК "
            f"(до ~12, если каналы у одного человека). "
            f"Лимит Discord — {cls.DISCORD_COMPONENT_LIMIT} компонентов."
        )

    async def list_all(self, guild_id: int) -> list[TgChannel]:
        return await self.db.list_tg_channels(guild_id)

    def build_board(
        self,
        guild: discord.Guild,
        channels: list[TgChannel],
        *,
        total_count: int | None = None,
    ) -> ui.LayoutView:
        view = ui.LayoutView(timeout=None)
        ordered = self.board_order(channels)

        header = ui.Container(accent_color=BRAND_COLOR)
        header_lines = ["## ТГК участников"]
        if total_count is not None and total_count > len(ordered):
            extra = total_count - len(ordered)
            header_lines.append(f"-# на доске {len(ordered)} из {total_count} · ещё {extra} не влезли")
        header.add_item(ui.TextDisplay("\n".join(header_lines)))
        view.add_item(header)

        if not ordered:
            empty = ui.Container(accent_color=BRAND_COLOR)
            empty.add_item(ui.TextDisplay("Пока пусто. `/tgk add` — добавить свой канал."))
            view.add_item(empty)
            return view

        last_user: int | None = None
        items_container: ui.Container | None = None

        for display_number, channel in enumerate(ordered, start=1):
            if channel.user_id != last_user:
                if items_container is not None:
                    view.add_item(items_container)
                    items_container = None
                sep = ui.Container(accent_color=BRAND_COLOR)
                sep.add_item(ui.TextDisplay(self._owner_separator(guild, channel.user_id)))
                view.add_item(sep)
                last_user = channel.user_id
                items_container = ui.Container(accent_color=BRAND_COLOR)

            title = self._channel_title(display_number, channel)
            link = self._channel_link(channel)
            if items_container is None:
                items_container = ui.Container(accent_color=BRAND_COLOR)
            if channel.image_url:
                items_container.add_item(
                    ui.Section(
                        ui.TextDisplay(title),
                        ui.TextDisplay(link),
                        accessory=ui.Thumbnail(
                            media=channel.image_url,
                            description=channel.title[:256],
                        ),
                    )
                )
            else:
                items_container.add_item(ui.TextDisplay(f"{title}\n{link}"))

        if items_container is not None:
            view.add_item(items_container)
        return view

    def _select_board_channels(self, channels: list[TgChannel]) -> tuple[list[TgChannel], int]:
        ordered = self.board_order(channels)
        total = len(ordered)
        if not ordered:
            return [], 0
        owner_count = len({channel.user_id for channel in ordered})
        limit = self.max_channels_for_owners(owner_count)
        limit = min(limit, self.MAX_BOARD_CHANNELS)
        while limit > 0:
            shown = ordered[:limit]
            owners = len({channel.user_id for channel in shown})
            if self.estimate_component_count(len(shown), owners) <= self.DISCORD_COMPONENT_LIMIT:
                return shown, total
            limit -= 1
        return ordered[:1], total

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
        all_channels = await self.list_all(guild.id)
        ordered = self.board_order(all_channels)
        if ordered:
            await self.db.renumber_tg_channels_ordered(guild.id, [channel.id for channel in ordered])
            all_channels = await self.list_all(guild.id)
            ordered = self.board_order(all_channels)
        shown, total = self._select_board_channels(ordered)
        view = self.build_board(guild, shown, total_count=total if total > len(shown) else None)
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
