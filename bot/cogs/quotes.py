"""Quote slash commands and context menu."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.bot import ErundaBot
from bot.utils.embeds import base_embed, error_embed, success_embed


class QuotesCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    quote = app_commands.Group(name="quote", description="Цитаты")

    @quote.command(name="add", description="Добавить цитату вручную")
    @app_commands.describe(
        text="Текст цитаты",
        author="Участник (необязательно, для поиска по /quote user)",
        name="Имя для отображения (без @, необязательно)",
    )
    @app_commands.guild_only()
    async def quote_add(
        self,
        interaction: discord.Interaction,
        text: str,
        author: discord.Member | None = None,
        name: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        display = name.strip() if name else None
        author_id = author.id if author else 0
        if author is None and not display:
            await interaction.response.send_message(
                embed=error_embed("Укажи имя автора или выбери участника"),
                ephemeral=True,
            )
            return
        if author is not None and display is None:
            display = author.display_name
        try:
            quote = await self.bot.quote_service.add_text(
                interaction.guild.id,
                text,
                interaction.user.id,
                author_id=author_id,
                author_display=display,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self.bot.quote_service.format_quote_embed(quote, interaction.guild),
        )

    @quote.command(name="edit", description="Изменить цитату по номеру")
    @app_commands.describe(
        quote_id="Номер цитаты (см. /quote list или footer у цитаты)",
        text="Новый текст",
        author="Новый автор для поиска (необязательно)",
        name="Новое имя для отображения (без @, необязательно)",
        date="Новая дата (ДД.ММ.ГГГГ, необязательно)",
    )
    @app_commands.guild_only()
    async def quote_edit(
        self,
        interaction: discord.Interaction,
        quote_id: int,
        text: str | None = None,
        author: discord.Member | None = None,
        name: str | None = None,
        date: str | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if text is None and author is None and name is None and date is None:
            await interaction.response.send_message(
                embed=error_embed("Укажи текст, имя, автора или дату для изменения"),
                ephemeral=True,
            )
            return

        update_author_id = author is not None
        update_author_display = name is not None or author is not None
        author_display: str | None = None
        author_id: int | None = None
        if author is not None:
            author_id = author.id
            author_display = name.strip() if name else author.display_name
        elif name is not None:
            author_display = name.strip()

        created_at: str | None = None
        if date is not None:
            try:
                created_at = self.bot.quote_service.parse_date(date)
            except ValueError as exc:
                await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
                return

        try:
            quote = await self.bot.quote_service.update(
                interaction.guild.id,
                quote_id,
                interaction.user,
                content=text,
                author_id=author_id,
                author_display=author_display,
                update_author_id=update_author_id,
                update_author_display=update_author_display,
                created_at=created_at,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=self.bot.quote_service.format_quote_embed(quote, interaction.guild),
        )

    @quote.command(name="delete", description="Удалить цитату по номеру")
    @app_commands.describe(quote_id="Номер цитаты")
    @app_commands.guild_only()
    async def quote_delete(
        self,
        interaction: discord.Interaction,
        quote_id: int,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        try:
            await self.bot.quote_service.delete(
                interaction.guild.id,
                quote_id,
                interaction.user,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"Цитата #{quote_id} удалена"),
            ephemeral=True,
        )

    @quote.command(name="random", description="Случайная цитата")
    @app_commands.guild_only()
    async def quote_random(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        quote = await self.bot.quote_service.random(interaction.guild.id)
        if quote is None:
            await interaction.response.send_message(
                embed=base_embed(title="Цитаты", description="Пока нет сохранённых цитат."),
            )
            return
        await interaction.response.send_message(
            embed=self.bot.quote_service.format_quote_embed(quote, interaction.guild),
        )

    @quote.command(name="list", description="Последние цитаты")
    @app_commands.guild_only()
    async def quote_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        quotes = await self.bot.quote_service.list_quotes(interaction.guild.id, limit=5)
        if not quotes:
            await interaction.response.send_message(
                embed=base_embed(title="Цитаты", description="Пока пусто."),
            )
            return
        embed = base_embed(title="Последние цитаты")
        for q in quotes:
            preview = q.content if len(q.content) <= 80 else q.content[:77] + "…"
            author = self.bot.quote_service.author_label(q, interaction.guild)
            embed.add_field(
                name=f"#{q.id}",
                value=f'"{preview}" — {author}',
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @quote.command(name="user", description="Цитаты участника")
    @app_commands.describe(user="Участник")
    @app_commands.guild_only()
    async def quote_user(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if interaction.guild is None:
            return
        quotes = await self.bot.quote_service.list_quotes(
            interaction.guild.id,
            author_id=user.id,
            limit=5,
        )
        if not quotes:
            await interaction.response.send_message(
                embed=base_embed(
                    title=f"Цитаты — {user.display_name}",
                    description="Ничего не найдено.",
                ),
            )
            return
        embed = base_embed(title=f"Цитаты — {user.display_name}")
        for q in quotes:
            preview = q.content if len(q.content) <= 100 else q.content[:97] + "…"
            embed.add_field(name=f"#{q.id}", value=f'"{preview}"', inline=False)
        await interaction.response.send_message(embed=embed)


@app_commands.context_menu(name="Add quote")
@app_commands.guild_only()
async def add_quote_context(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    bot = interaction.client
    if not isinstance(bot, ErundaBot):
        return
    if message.guild is None or message.author.bot:
        await interaction.response.send_message(
            embed=error_embed("Нельзя цитировать это сообщение"),
            ephemeral=True,
        )
        return
    try:
        quote = await bot.quote_service.add_from_message(message, interaction.user.id)
    except ValueError as exc:
        await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
        return
    await interaction.response.send_message(
        embed=success_embed("Цитата сохранена"),
        ephemeral=True,
    )
    config = await bot.config_service.get(message.guild.id)
    if config.quotes_channel_id:
        channel = message.guild.get_channel(config.quotes_channel_id)
        if channel and hasattr(channel, "send"):
            await channel.send(embed=bot.quote_service.format_quote_embed(quote, message.guild))


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(QuotesCog(bot))
    bot.tree.add_command(add_quote_context)
