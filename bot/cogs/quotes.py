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
