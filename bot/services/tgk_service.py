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
    r'<meta[^>]+(?:property|name)=["\'](?P<key>og:(?:title|image|description))["\'][^>]+content=["\'](?P<content>[^"\']+)["\']',
    re.IGNORECASE,
)
OG_META_RE_ALT = re.compile(
    r'<meta[^>]+content=["\'](?P<content>[^"\']+)["\'][^>]+(?:property|name)=["\'](?P<key>og:(?:title|image|description))["\']',
    re.IGNORECASE,
)
PAGE_TITLE_RE = re.compile(
    r'<div[^>]+class=["\']tgme_page_title["\'][^>]*>\s*<span[^>]*>(?P<title>.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
PAGE_DESCRIPTION_RE = re.compile(
    r'<div[^>]+class=["\']tgme_page_description[^"\']*["\'][^>]*>(?P<desc>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class TgChannelMeta:
    title: str
    image_url: str | None = None
    description: str | None = None


class TgkService:
    DISCORD_COMPONENT_LIMIT = 40
    MAX_BOARD_CHANNELS = 10
    # ASCII `-` is narrow; need enough to fill the card, but >~76 wraps on desktop.
    OWNER_SEPARATOR_MIN = 68
    OWNER_SEPARATOR_MAX = 74
    DESCRIPTION_STORE_MAX = 512
    DESCRIPTION_DISPLAY_MAX = 160

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

    @classmethod
    def _clean_description(cls, raw: str) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""
        return text[: cls.DESCRIPTION_STORE_MAX]

    @classmethod
    def _trim_display_description(cls, description: str) -> str:
        text = re.sub(r"\s+", " ", description.strip())
        if len(text) <= cls.DESCRIPTION_DISPLAY_MAX:
            return text
        return text[: cls.DESCRIPTION_DISPLAY_MAX - 1].rstrip() + "…"

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
        og_description: str | None = None
        for pattern in (OG_META_RE, OG_META_RE_ALT):
            for match in pattern.finditer(html_text):
                key = match.group("key").lower()
                content = html.unescape(match.group("content").strip())
                if key == "og:title" and og_title is None:
                    og_title = content
                elif key == "og:image" and og_image is None:
                    og_image = cls._normalize_image_url(content)
                elif key == "og:description" and og_description is None:
                    og_description = cls._clean_description(content)

        title = og_title
        if not title:
            page_match = PAGE_TITLE_RE.search(html_text)
            if page_match:
                title = re.sub(r"<[^>]+>", "", page_match.group("title"))
        cleaned = cls._clean_title(title or fallback, fallback)

        description = og_description
        if not description:
            page_desc = PAGE_DESCRIPTION_RE.search(html_text)
            if page_desc:
                description = cls._clean_description(page_desc.group("desc"))
        if not description:
            description = None

        return TgChannelMeta(title=cleaned, image_url=og_image, description=description)

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
            meta.description,
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

    @classmethod
    def _centered_dashes(cls, center: str, width: int) -> str:
        width = max(cls.OWNER_SEPARATOR_MIN, min(cls.OWNER_SEPARATOR_MAX, width))
        center_len = len(center)
        if center_len >= width:
            return center.strip()
        pad = width - center_len
        left = pad // 2
        right = pad - left
        return f"{'-' * left}{center}{'-' * right}"

    @classmethod
    def _page_separator_width(cls, guild: discord.Guild, channels: list[TgChannel]) -> int:
        """One shared width for all owner rows on the page (fills card, no wrap)."""
        needed = cls.OWNER_SEPARATOR_MIN
        for user_id, _group in cls._groups_in_order(channels):
            member = guild.get_member(user_id)
            username = member.name if member is not None else f"user{user_id}"
            # " name " + at least ~10 dashes each side
            needed = max(needed, len(username) + 2 + 20)
        return max(cls.OWNER_SEPARATOR_MIN, min(cls.OWNER_SEPARATOR_MAX, needed))

    @classmethod
    def _owner_separator(cls, guild: discord.Guild, user_id: int, *, width: int) -> str:
        member = guild.get_member(user_id)
        username = member.name if member is not None else f"user{user_id}"
        return cls._centered_dashes(f" {username} ", width)

    @staticmethod
    def _channel_title(display_number: int, item: TgChannel) -> str:
        return f"# {display_number}.  {item.title}"

    @staticmethod
    def _channel_details(item: TgChannel) -> str:
        kind = "приватка" if TgkService.is_private_url(item.url) else "открытый"
        lines: list[str] = []
        if item.description:
            desc = TgkService._trim_display_description(item.description)
            if desc:
                lines.append(f"-# {desc}")
        lines.append(f"[({kind})]({item.url})")
        return "\n".join(lines)

    @staticmethod
    def board_display_order(channels: list[TgChannel]) -> list[TgChannel]:
        grouped: dict[int, list[TgChannel]] = {}
        for entry in channels:
            grouped.setdefault(entry.user_id, []).append(entry)
        for user_id in grouped:
            grouped[user_id].sort(key=lambda c: (c.number, c.id))
        user_ids = sorted(grouped.keys(), key=lambda uid: grouped[uid][0].number)
        ordered: list[TgChannel] = []
        for user_id in user_ids:
            ordered.extend(grouped[user_id])
        return ordered

    @staticmethod
    def _groups_in_order(channels: list[TgChannel]) -> list[tuple[int, list[TgChannel]]]:
        groups: list[tuple[int, list[TgChannel]]] = []
        for entry in channels:
            if groups and groups[-1][0] == entry.user_id:
                groups[-1][1].append(entry)
            else:
                groups.append((entry.user_id, [entry]))
        return groups

    @classmethod
    def estimate_page_components(cls, channels: list[TgChannel], *, with_header: bool = True) -> int:
        """Rough hint only; use can_build_page for splitting."""
        if not channels:
            return 2 if with_header else 0
        cost = 2 if with_header else 0
        for _user_id, user_channels in cls._groups_in_order(channels):
            cost += 3
            for entry in user_channels:
                cost += 4 if entry.image_url else 2
        return cost

    def can_build_page(
        self,
        guild: discord.Guild,
        channels: list[TgChannel],
        *,
        page_index: int = 0,
        page_count: int | None = None,
        show_actions: bool = False,
        bot=None,
    ) -> bool:
        total_pages = page_count if page_count is not None else max(page_index + 1, 2)
        if not channels and show_actions and bot is not None:
            try:
                self.build_board_page(
                    guild,
                    [],
                    page_index=page_index,
                    page_count=total_pages,
                    show_actions=True,
                    bot=bot,
                )
            except ValueError:
                return False
            return True
        if not channels:
            return True
        try:
            self.build_board_page(
                guild,
                channels,
                page_index=page_index,
                page_count=total_pages,
                show_actions=show_actions,
                bot=bot if show_actions else None,
            )
        except ValueError:
            return False
        return True

    @classmethod
    def board_capacity_hint(cls) -> str:
        return "В одно сообщение ~8 ТГК с превью; если больше — несколько сообщений подряд."

    async def list_all(self, guild_id: int) -> list[TgChannel]:
        return await self.db.list_tg_channels(guild_id)

    async def list_for_user(self, guild_id: int, user_id: int) -> list[TgChannel]:
        return [entry for entry in await self.list_all(guild_id) if entry.user_id == user_id]

    async def refresh_guild_meta(self, guild_id: int) -> int:
        channels = await self.list_all(guild_id)
        updated = 0
        for channel in channels:
            try:
                meta = await asyncio.to_thread(self.fetch_channel_meta, channel.url)
            except ValueError:
                log.warning(
                    "TGK meta refresh failed for channel %s in guild %s",
                    channel.id,
                    guild_id,
                )
                continue
            if (
                meta.title == channel.title
                and meta.image_url == channel.image_url
                and meta.description == channel.description
            ):
                continue
            await self.db.update_tg_channel_meta(
                channel.id,
                guild_id,
                meta.title,
                meta.image_url,
                meta.description,
            )
            updated += 1
            await asyncio.sleep(0.35)
        if updated:
            log.info("Refreshed TGK meta for %s channels in guild %s", updated, guild_id)
        return updated

    def build_board_page(
        self,
        guild: discord.Guild,
        channels: list[TgChannel],
        *,
        start_display_number: int = 1,
        page_index: int = 0,
        page_count: int = 1,
        show_actions: bool = False,
        bot=None,
    ) -> ui.LayoutView:
        view = ui.LayoutView(timeout=None)
        ordered = list(channels)

        header = ui.Container(accent_color=BRAND_COLOR)
        if page_index == 0:
            title = "## ТГК участников"
            if page_count > 1:
                title += f"\n-# часть 1 из {page_count}"
            header_lines = [title]
        else:
            header_lines = [f"## ТГК участников\n-# часть {page_index + 1} из {page_count}"]
        header.add_item(ui.TextDisplay("\n".join(header_lines)))
        view.add_item(header)

        if not ordered:
            empty = ui.Container(accent_color=BRAND_COLOR)
            empty.add_item(ui.TextDisplay("Пока пусто. Жми «Добавить ТГК» ниже."))
            view.add_item(empty)
            if show_actions and bot is not None:
                from bot.views.tgk_views import append_tgk_board_actions

                append_tgk_board_actions(view, bot, guild.id)
            return view

        display_number = start_display_number
        sep_width = self._page_separator_width(guild, ordered)
        for user_id, user_channels in self._groups_in_order(ordered):
            sep = ui.Container(accent_color=BRAND_COLOR)
            sep.add_item(
                ui.TextDisplay(self._owner_separator(guild, user_id, width=sep_width))
            )
            view.add_item(sep)

            block = ui.Container(accent_color=BRAND_COLOR)
            for entry in user_channels:
                title = self._channel_title(display_number, entry)
                details = self._channel_details(entry)
                if entry.image_url:
                    block.add_item(
                        ui.Section(
                            ui.TextDisplay(title),
                            ui.TextDisplay(details),
                            accessory=ui.Thumbnail(
                                media=entry.image_url,
                                description=entry.title[:256],
                            ),
                        )
                    )
                else:
                    block.add_item(ui.TextDisplay(f"{title}\n{details}"))
                display_number += 1
            view.add_item(block)

        if show_actions and bot is not None:
            from bot.views.tgk_views import append_tgk_board_actions

            append_tgk_board_actions(view, bot, guild.id)
        return view

    def _pack_pages_without_actions(
        self,
        guild: discord.Guild,
        ordered: list[TgChannel],
    ) -> list[list[TgChannel]]:
        pages: list[list[TgChannel]] = []
        current: list[TgChannel] = []

        for _group_user_id, group_channels in self._groups_in_order(ordered):
            group = list(group_channels)
            while group:
                page_index = len(pages) if not current else len(pages)
                trial = current + group
                if current and not self.can_build_page(guild, trial, page_index=page_index):
                    pages.append(current)
                    current = []
                    continue
                if self.can_build_page(guild, trial, page_index=page_index):
                    current = trial
                    group = []
                    continue
                if not current:
                    take = len(group)
                    while take > 1 and not self.can_build_page(
                        guild,
                        group[:take],
                        page_index=page_index,
                    ):
                        take -= 1
                    chunk = group[:take]
                    if not self.can_build_page(guild, chunk, page_index=page_index):
                        log.error(
                            "TGK page chunk does not fit Discord limit (%s channels)",
                            len(chunk),
                        )
                        break
                    pages.append(chunk)
                    group = group[take:]
                    continue
                pages.append(current)
                current = []

        if current:
            pages.append(current)
        return pages

    def _ensure_last_page_actions_fit(
        self,
        guild: discord.Guild,
        pages: list[list[TgChannel]],
        bot,
    ) -> list[list[TgChannel]]:
        if bot is None or not pages:
            return pages
        while pages:
            last_idx = len(pages) - 1
            page_count = len(pages)
            if self.can_build_page(
                guild,
                pages[last_idx],
                page_index=last_idx,
                page_count=page_count,
                show_actions=True,
                bot=bot,
            ):
                return pages
            last = pages[-1]
            if len(last) <= 1:
                log.error(
                    "TGK last page cannot fit action buttons in guild %s",
                    guild.id,
                )
                return pages
            moved = last.pop()
            if not last:
                pages.pop()
            pages.append([moved])
        return pages

    def split_into_pages(self, guild: discord.Guild, channels: list[TgChannel], bot=None) -> list[list[TgChannel]]:
        ordered = self.board_display_order(channels)
        if not ordered:
            return [[]]

        pages = self._pack_pages_without_actions(guild, ordered)
        if bot is not None:
            pages = self._ensure_last_page_actions_fit(guild, pages, bot)

        packed = sum(len(page) for page in pages)
        if packed != len(ordered):
            log.error(
                "TGK page split lost entries: %s packed of %s",
                packed,
                len(ordered),
            )
        return pages

    async def sync_board(
        self,
        guild: discord.Guild,
        bot,
        fallback_channel: discord.abc.Messageable | None = None,
    ) -> list[discord.Message]:
        config = await self.db.get_guild(guild.id)
        post_channel = None
        official = False
        if config is not None and config.tgk_channel_id:
            found = guild.get_channel(config.tgk_channel_id)
            if found is not None and hasattr(found, "send"):
                post_channel = found
                official = True
        if post_channel is None:
            post_channel = fallback_channel
        if post_channel is None or not hasattr(post_channel, "send"):
            return []

        all_channels = await self.list_all(guild.id)
        ordered = self.board_display_order(all_channels)
        if ordered:
            await self.db.renumber_tg_channels_ordered(
                guild.id,
                [entry.id for entry in ordered],
            )
            ordered = self.board_display_order(await self.list_all(guild.id))

        pages = self.split_into_pages(guild, ordered, bot)
        if sum(len(page) for page in pages) != len(ordered):
            log.error("TGK sync aborted: page split mismatch for guild %s", guild.id)
            return []

        stored_ids = await self.db.get_tgk_board_message_ids(guild.id) if official else []
        messages: list[discord.Message] = []
        display_offset = 0

        for page_index, page_channels in enumerate(pages):
            is_last_page = page_index == len(pages) - 1
            try:
                view = self.build_board_page(
                    guild,
                    page_channels,
                    start_display_number=display_offset + 1,
                    page_index=page_index,
                    page_count=len(pages),
                    show_actions=is_last_page,
                    bot=bot if is_last_page else None,
                )
            except ValueError:
                log.exception(
                    "TGK board page %s exceeds Discord component limit in guild %s",
                    page_index + 1,
                    guild.id,
                )
                continue
            display_offset += len(page_channels)
            message: discord.Message | None = None
            if official and page_index < len(stored_ids):
                try:
                    message = await post_channel.fetch_message(stored_ids[page_index])
                    await message.edit(content=None, embeds=[], view=view)
                except discord.DiscordException:
                    log.warning(
                        "Failed to edit TGK board page %s in guild %s",
                        page_index + 1,
                        guild.id,
                    )
                    message = None
            if message is None:
                try:
                    message = await post_channel.send(view=view)
                except discord.DiscordException:
                    log.exception(
                        "Failed to send TGK board page %s in guild %s",
                        page_index + 1,
                        guild.id,
                    )
                    continue
            messages.append(message)
            if official and is_last_page:
                from bot.views.tgk_views import bind_tgk_board_view

                bind_tgk_board_view(bot, guild.id, message.id)

        if official and messages:
            for stale_id in stored_ids[len(messages) :]:
                try:
                    stale = await post_channel.fetch_message(stale_id)
                    await stale.delete()
                except discord.DiscordException:
                    pass
            await self.db.set_tgk_board_message_ids(guild.id, [message.id for message in messages])
            log.info(
                "Synced TGK board in guild %s: %s channels, %s messages",
                guild.id,
                len(ordered),
                len(messages),
            )

        return messages
