"""Quote business logic."""

from __future__ import annotations

import json
from datetime import datetime

import discord

from bot.database.database import Database
from bot.database.models import Quote


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
        return base_embed(title="💬 Цитата", description=body)
