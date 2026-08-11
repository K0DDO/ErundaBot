"""Quote business logic."""

from __future__ import annotations

import json
from datetime import datetime

import discord

from bot.database.database import Database
from bot.database.models import Quote
from bot.utils.permissions import is_guild_admin


class QuoteService:
    def __init__(self, db: Database) -> None:
        self.db = db

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
        if not message.content.strip():
            raise ValueError("Пустое сообщение нельзя сохранить")
        created = message.created_at.isoformat() if message.created_at else None
        display = author_display or message.author.display_name
        return await self.db.add_quote(
            message.guild.id,
            message.content,
            message.author.id,
            added_by,
            message.channel.id,
            message.id,
            created,
            self.reactions_snapshot(message),
            author_display=display,
        )

    async def add_text(
        self,
        guild_id: int,
        content: str,
        added_by: int,
        *,
        author_id: int = 0,
        author_display: str | None = None,
    ) -> Quote:
        if not content.strip():
            raise ValueError("Текст не может быть пустым")
        if author_id == 0 and not author_display:
            raise ValueError("Укажи имя автора или выбери участника")
        return await self.db.add_quote(
            guild_id,
            content.strip(),
            author_id,
            added_by,
            None,
            None,
            datetime.utcnow().isoformat(),
            "{}",
            author_display=author_display,
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
        quote = await self.db.get_quote(quote_id)
        if quote is None or quote.guild_id != guild_id:
            return None
        return quote

    @staticmethod
    def can_manage(quote: Quote, member: discord.Member) -> bool:
        if is_guild_admin(member):
            return True
        if quote.added_by == member.id:
            return True
        return bool(quote.author_id and quote.author_id == member.id)

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
    ) -> Quote:
        quote = await self.get(guild_id, quote_id)
        if quote is None:
            raise ValueError("Цитата не найдена")
        if not self.can_manage(quote, member):
            raise ValueError("Нет прав изменить эту цитату")

        if content is not None:
            text = content.strip()
            if not text:
                raise ValueError("Текст не может быть пустым")
            quote.content = text
        if update_author_id:
            quote.author_id = author_id or 0
        if update_author_display:
            quote.author_display = author_display
        if created_at is not None:
            quote.created_at = created_at
        if quote.author_id == 0 and not quote.author_display:
            raise ValueError("Укажи имя автора или выбери участника")

        await self.db.save_quote(quote)
        return quote

    async def delete(self, guild_id: int, quote_id: int, member: discord.Member) -> None:
        quote = await self.get(guild_id, quote_id)
        if quote is None:
            raise ValueError("Цитата не найдена")
        if not self.can_manage(quote, member):
            raise ValueError("Нет прав удалить эту цитату")
        deleted = await self.db.delete_quote(quote_id, guild_id)
        if not deleted:
            raise ValueError("Цитата не найдена")

    def format_quote_embed(
        self,
        quote: Quote,
        guild: discord.Guild | None = None,
    ) -> discord.Embed:
        from bot.utils.embeds import base_embed

        reactions = self.format_reactions(quote.reactions_snapshot)
        date_part = ""
        if quote.created_at:
            try:
                dt = datetime.fromisoformat(quote.created_at)
                date_part = dt.strftime("%d.%m.%Y")
            except ValueError:
                date_part = quote.created_at[:10]
        author = self.author_label(quote, guild)
        body = f'"{quote.content}"\n\n— {author}'
        if date_part:
            body += f"\n{date_part}"
        if reactions:
            body += f"\n\n{reactions}"
        embed = base_embed(title="💬 Цитата", description=body)
        embed.set_footer(text=f"#{quote.id}")
        return embed
