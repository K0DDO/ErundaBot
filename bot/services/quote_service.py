"""Quote business logic."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import discord

from bot.database.database import Database
from bot.database.models import Quote
from bot.utils.permissions import is_guild_admin

log = logging.getLogger(__name__)

QUOTE_FOOTER_RE = re.compile(r"^#(\d+)$")
QUOTE_HEADER_RE = re.compile(r"^\*{0,2}#(\d+)\*{0,2}\s*$", re.MULTILINE)
QUOTE_NUMBER_RE = re.compile(r"#(\d+)")
MONTHS_RU = (
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


class QuoteService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def normalize_quote_text(text: str) -> str:
        cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = cleaned.replace("\\n", "\n")
        lines = [line.rstrip() for line in cleaned.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines).strip()

    @staticmethod
    def preview_text(content: str, limit: int = 80) -> str:
        flat = " ".join(content.split())
        if len(flat) <= limit:
            return flat
        return flat[: limit - 1] + "…"

    @staticmethod
    def reactions_snapshot(message: discord.Message) -> str:
        data: dict[str, int] = {}
        for reaction in message.reactions:
            emoji = str(reaction.emoji)
            data[emoji] = reaction.count
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def format_reactions(snapshot_json: str) -> str:
        try:
            data: dict[str, int] = json.loads(snapshot_json or "{}")
        except json.JSONDecodeError:
            return ""
        if not data:
            return ""
        parts = [f"{emoji} {count}" for emoji, count in data.items()]
        return "   ".join(parts)

    @staticmethod
    def author_label(quote: Quote, guild: discord.Guild | None = None) -> str:
        if quote.author_display:
            return quote.author_display
        if quote.author_id and guild is not None:
            member = guild.get_member(quote.author_id)
            if member is not None:
                return member.display_name
        if quote.author_id:
            return "участник"
        return "аноним"

    @staticmethod
    def format_quote_date(created_at: str | None) -> str:
        if not created_at:
            return ""
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            month = MONTHS_RU[parsed.month] if 1 <= parsed.month <= 12 else ""
            if not month:
                return parsed.strftime("%d.%m.%Y")
            return f"{parsed.day} {month} {parsed.year}"
        except ValueError:
            return created_at[:10]

    def author_avatar_url(self, quote: Quote, guild: discord.Guild | None) -> str | None:
        if guild is None:
            return None
        for author_id in quote.linked_author_ids():
            member = guild.get_member(author_id)
            if member is not None:
                return member.display_avatar.with_size(64).url
        return None

    @staticmethod
    def collect_author_ids(*members: discord.Member | None) -> list[int]:
        ids: list[int] = []
        for member in members:
            if member is not None and member.id not in ids:
                ids.append(member.id)
        return ids

    def _quote_card_kwargs(self, quote: Quote, guild: discord.Guild | None = None) -> dict:
        author = self.author_label(quote, guild)
        date_part = self.format_quote_date(quote.created_at)
        content = discord.utils.escape_markdown(quote.content)
        avatar_url = self.author_avatar_url(quote, guild)
        return {
            "number_text": f"-# *#{quote.number}*",
            "quote_text": "\n".join(f"## {line}" for line in f"«{content}»".split("\n")),
            "author_text": f"**{discord.utils.escape_markdown(author)}**",
            "date_text": f"-# *{date_part}*" if date_part else None,
            "reactions_text": self.format_reactions(quote.reactions_snapshot),
            "avatar_url": avatar_url,
            "avatar_description": author[:256] if avatar_url else None,
        }

    def build_quote_card(self, quote: Quote, guild: discord.Guild | None = None):
        from bot.views.quote_views import QuoteCardView

        return QuoteCardView(**self._quote_card_kwargs(quote, guild))

    def build_quote_delete_view(
        self,
        quote: Quote,
        guild: discord.Guild,
        requester_id: int,
        bot,
    ):
        from bot.views.quote_views import (
            QuoteCardView,
            QuoteDeleteCancelButton,
            QuoteDeleteConfirmButton,
        )

        row = discord.ui.ActionRow()
        row.add_item(QuoteDeleteConfirmButton(bot, guild.id, quote.number, requester_id))
        row.add_item(QuoteDeleteCancelButton(requester_id))
        return QuoteCardView(
            **self._quote_card_kwargs(quote, guild),
            footer_text="**Удалить эту цитату?**",
            extra_row=row,
            timeout=120,
        )

    @staticmethod
    def parse_date(value: str) -> str:
        cleaned = value.strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            except ValueError:
                continue
        raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ")

    async def add_from_message(
        self,
        message: discord.Message,
        added_by: int,
        *,
        author_display: str | None = None,
    ) -> Quote:
        if message.guild is None:
            raise ValueError("Только сообщения сервера")
        content = self.normalize_quote_text(message.content)
        if not content:
            raise ValueError("Пустое сообщение нельзя сохранить")
        created = message.created_at.isoformat() if message.created_at else None
        display = author_display or message.author.display_name
        return await self.db.add_quote(
            message.guild.id,
            content,
            message.author.id,
            added_by,
            message.channel.id,
            message.id,
            created,
            self.reactions_snapshot(message),
            author_display=display,
            author_ids=[message.author.id],
        )

    async def add_text(
        self,
        guild_id: int,
        content: str,
        added_by: int,
        *,
        author_id: int = 0,
        author_display: str | None = None,
        created_at: str | None = None,
        author_ids: list[int] | None = None,
    ) -> Quote:
        text = self.normalize_quote_text(content)
        if not text:
            raise ValueError("Текст не может быть пустым")
        if not author_display or not author_display.strip():
            raise ValueError("Укажи имя на карточке")
        linked = list(author_ids or [])
        if author_id and author_id not in linked:
            linked.insert(0, author_id)
        return await self.db.add_quote(
            guild_id,
            text,
            linked[0] if linked else 0,
            added_by,
            None,
            None,
            created_at or datetime.utcnow().isoformat(),
            "{}",
            author_display=author_display,
            author_ids=linked,
        )

    async def random(self, guild_id: int) -> Quote | None:
        return await self.db.random_quote(guild_id)

    async def list_quotes(
        self,
        guild_id: int,
        author_id: int | None = None,
        limit: int = 10,
    ) -> list[Quote]:
        return await self.db.list_quotes(guild_id, author_id, limit)

    async def get(self, guild_id: int, quote_id: int) -> Quote | None:
        return await self.db.get_quote_by_number(guild_id, quote_id)

    @staticmethod
    def can_manage(quote: Quote, member: discord.Member) -> bool:
        if is_guild_admin(member):
            return True
        if quote.added_by == member.id:
            return True
        return member.id in quote.linked_author_ids()

    async def update(
        self,
        guild_id: int,
        quote_id: int,
        member: discord.Member,
        *,
        content: str | None = None,
        author_id: int | None = None,
        author_display: str | None = None,
        update_author_id: bool = False,
        update_author_display: bool = False,
        created_at: str | None = None,
        author_ids: list[int] | None = None,
    ) -> Quote:
        quote = await self.get(guild_id, quote_id)
        if quote is None:
            raise ValueError("Цитата не найдена")
        if not self.can_manage(quote, member):
            raise ValueError("Нет прав изменить эту цитату")

        if content is not None:
            text = self.normalize_quote_text(content)
            if not text:
                raise ValueError("Текст не может быть пустым")
            quote.content = text
        if update_author_id:
            linked = list(author_ids or [])
            if author_id and author_id not in linked:
                linked.insert(0, author_id)
            quote.author_ids = linked
            quote.author_id = linked[0] if linked else 0
        if update_author_display:
            quote.author_display = author_display
        if created_at is not None:
            quote.created_at = created_at
        if not quote.author_display:
            raise ValueError("Укажи имя на карточке")

        await self.db.save_quote(quote)
        return quote

    @staticmethod
    def _component_texts(component: object) -> list[str]:
        texts: list[str] = []
        content = getattr(component, "content", None)
        if isinstance(content, str) and content.strip():
            texts.append(content)
        for attr in ("children", "components", "items"):
            children = getattr(component, attr, None)
            if not children:
                continue
            try:
                iterable = list(children)
            except TypeError:
                continue
            for child in iterable:
                texts.extend(QuoteService._component_texts(child))
        return texts

    def quote_id_from_message(self, message: discord.Message) -> int | None:
        if message.embeds:
            embed = message.embeds[0]
            footer = embed.footer.text if embed.footer else None
            if footer:
                match = QUOTE_FOOTER_RE.match(footer.strip())
                if match:
                    return int(match.group(1))
        for component in message.components:
            for text in self._component_texts(component):
                match = QUOTE_HEADER_RE.search(text)
                if match:
                    return int(match.group(1))
                match = QUOTE_NUMBER_RE.search(text)
                if match:
                    return int(match.group(1))
        return None

    def _is_legacy_quote_message(self, message: discord.Message) -> bool:
        if not message.embeds:
            return False
        embed = message.embeds[0]
        title = embed.title or ""
        footer = embed.footer.text if embed.footer else ""
        if "Цитата" in title:
            return True
        return bool(footer and QUOTE_FOOTER_RE.match(footer.strip()))

    def _looks_like_quote_message(self, message: discord.Message) -> bool:
        if self.quote_id_from_message(message) is not None:
            return True
        if message.embeds:
            title = message.embeds[0].title or ""
            if "Цитата" in title:
                return True
        return False

    async def publish_to_channel(
        self,
        guild: discord.Guild,
        quote: Quote,
        channel: discord.TextChannel,
    ) -> Quote:
        message = await channel.send(view=self.build_quote_card(quote, guild))
        await self.db.set_quote_posted_message(quote.id, channel.id, message.id)
        quote.posted_channel_id = channel.id
        quote.posted_message_id = message.id
        return quote

    async def sync_posted_message(
        self,
        guild: discord.Guild,
        quote: Quote,
        *,
        only_legacy: bool = False,
    ) -> bool:
        if quote.posted_channel_id is None or quote.posted_message_id is None:
            return False
        channel = guild.get_channel(quote.posted_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            message = await channel.fetch_message(quote.posted_message_id)
        except discord.NotFound:
            quote.posted_message_id = None
            quote.posted_channel_id = None
            return False
        except discord.HTTPException:
            log.warning("Failed to fetch quote message #%s in guild %s", quote.number, guild.id)
            return False
        if only_legacy and not self._is_legacy_quote_message(message):
            return False
        view = self.build_quote_card(quote, guild)
        try:
            await message.edit(content=None, embeds=[], view=view)
            return True
        except discord.HTTPException:
            log.warning("Failed to edit quote message #%s in guild %s", quote.number, guild.id)
            return False

    async def remove_posted_messages(self, guild: discord.Guild, quote: Quote) -> None:
        deleted: set[int] = set()

        async def try_delete(channel: discord.TextChannel, message_id: int) -> None:
            if message_id in deleted:
                return
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
                deleted.add(message_id)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.warning("Failed to delete quote message %s", message_id)

        if quote.posted_channel_id and quote.posted_message_id:
            channel = guild.get_channel(quote.posted_channel_id)
            if isinstance(channel, discord.TextChannel):
                await try_delete(channel, quote.posted_message_id)
                if quote.posted_message_id in deleted:
                    return

        config = await self.db.get_guild(guild.id)
        if config is None or config.quotes_channel_id is None:
            return
        channel = guild.get_channel(config.quotes_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        try:
            async for message in channel.history(limit=500):
                if guild.me is not None and message.author.id != guild.me.id:
                    continue
                quote_id = self.quote_id_from_message(message)
                if quote_id in {quote.number, quote.id}:
                    await try_delete(channel, message.id)
        except discord.HTTPException:
            log.exception("Failed to scan quotes channel in guild %s", guild.id)

    def _quote_from_footer(self, footer_num: int, quotes: list[Quote]) -> Quote | None:
        by_number = {quote.number: quote for quote in quotes}
        if footer_num in by_number:
            return by_number[footer_num]
        by_id = {quote.id: quote for quote in quotes}
        return by_id.get(footer_num)

    async def cleanup_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> int:
        quotes = await self.db.list_quotes(guild.id, limit=10_000)
        canonical: dict[int, int | None] = {
            q.id: q.posted_message_id for q in quotes
        }
        by_posted = {
            q.posted_message_id: q for q in quotes if q.posted_message_id is not None
        }
        kept: set[int] = set()
        removed = 0

        try:
            messages = [m async for m in channel.history(limit=500)]
        except discord.HTTPException:
            log.exception("Failed to read quotes channel history in guild %s", guild.id)
            return 0

        for message in reversed(messages):
            if guild.me is None or message.author.id != guild.me.id:
                continue
            quote = by_posted.get(message.id)
            if quote is None:
                footer_num = self.quote_id_from_message(message)
                if footer_num is not None:
                    quote = self._quote_from_footer(footer_num, quotes)
                elif not self._looks_like_quote_message(message):
                    continue
            if quote is None:
                try:
                    await message.delete()
                    removed += 1
                except discord.HTTPException:
                    pass
                continue
            canonical_id = canonical.get(quote.id)
            if canonical_id == message.id:
                kept.add(quote.id)
                continue
            if quote.id in kept:
                try:
                    await message.delete()
                    removed += 1
                except discord.HTTPException:
                    pass
                continue
            if canonical_id is None:
                await self.db.set_quote_posted_message(quote.id, channel.id, message.id)
                canonical[quote.id] = message.id
                quote.posted_channel_id = channel.id
                quote.posted_message_id = message.id
                kept.add(quote.id)
                continue
            try:
                await message.delete()
                removed += 1
            except discord.HTTPException:
                pass

        return removed

    async def migrate_legacy_cards(self, guild: discord.Guild) -> int:
        quotes = await self.db.list_quotes(guild.id, limit=10_000)
        migrated = 0
        for quote in quotes:
            if await self.sync_posted_message(guild, quote):
                migrated += 1
        return migrated

    async def renumber_and_sync(self, guild: discord.Guild) -> int:
        quotes = await self.db.renumber_quotes(guild.id)
        for quote in quotes:
            await self.sync_posted_message(guild, quote)
        return len(quotes)

    async def delete(
        self,
        guild: discord.Guild,
        guild_id: int,
        quote_id: int,
        member: discord.Member,
    ) -> None:
        quote = await self.get(guild_id, quote_id)
        if quote is None:
            raise ValueError("Цитата не найдена")
        if not self.can_manage(quote, member):
            raise ValueError("Нет прав удалить эту цитату")
        try:
            await self.remove_posted_messages(guild, quote)
        except discord.HTTPException:
            log.warning("Failed to remove posted quote #%s in guild %s", quote.number, guild.id)
        deleted = await self.db.delete_quote(quote.id, guild_id)
        if not deleted:
            raise ValueError("Цитата не найдена")
        try:
            await self.renumber_and_sync(guild)
        except discord.HTTPException:
            log.warning("Failed to renumber quotes after deleting #%s", quote.number)
