"""Telegram channel directory."""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.error
import urllib.request

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
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)


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

    def fetch_preview_image(self, url: str) -> str | None:
        username = self._username(url)
        if username is None:
            return None
        request = urllib.request.Request(
            f"https://t.me/{username}",
            headers={"User-Agent": "Mozilla/5.0 ErundaBot"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE_ALT.search(html)
        if match is None:
            return None
        image = match.group(1).strip()
        if image.startswith("//"):
            image = "https:" + image
        if not image.startswith("https://"):
            return None
        return image

    async def add(self, guild_id: int, user_id: int, title: str, raw_url: str) -> TgChannel:
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("Название пустое")
        url = self.normalize_url(raw_url)
        image_url = await asyncio.to_thread(self.fetch_preview_image, url)
        return await self.db.add_tg_channel(guild_id, user_id, cleaned_title, url, image_url)

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
