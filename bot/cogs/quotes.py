"""Quote slash commands and context menu."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.bot import ErundaBot
from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.views.quote_views import QuoteComposeModal, QuoteEditModal

log = logging.getLogger(__name__)


async def publish_quote(
    bot: ErundaBot,
    guild: discord.Guild,
    quote,
) -> discord.TextChannel | None:
    """Post quote embed to configured quotes channel, if set."""
    config = await bot.config_service.get(guild.id)
    if not config.quotes_channel_id:
        return None
    channel = guild.get_channel(config.quotes_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return None
    await bot.quote_service.publish_to_channel(guild, quote, channel)
    return channel


class QuotesCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._cleaned_guilds: set[int] = set()

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self._cleanup_all_guilds())

    async def _cleanup_all_guilds(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._cleanup_guild_quotes(guild)

    async def _cleanup_guild_quotes(self, guild: discord.Guild) -> None:
        if guild.id in self._cleaned_guilds:
            return
        removed = 0
        config = await self.bot.config_service.get(guild.id)
        if config.quotes_channel_id:
            channel = guild.get_channel(config.quotes_channel_id)
            if isinstance(channel, discord.TextChannel):
                removed = await self.bot.quote_service.cleanup_channel(guild, channel)
        migrated = await self.bot.quote_service.migrate_legacy_cards(guild)
        if removed or migrated:
            log.info(
                "Quotes cleanup in guild %s: removed=%s migrated=%s",
                guild.id,
                removed,
                migrated,
            )
        self._cleaned_guilds.add(guild.id)

    quote = app_commands.Group(name="quote", description="Цитаты")

    @quote.command(name="add", description="Добавить цитату вручную")
    @app_commands.describe(author="Участник (необязательно, для поиска по /quote user)")
    @app_commands.guild_only()
    async def quote_add(
        self,
        interaction: discord.Interaction,
        author: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_modal(
            QuoteComposeModal(self.bot, author=author, silent=False),
        )

    @quote.command(name="import", description="Добавить цитату в базу без публикации в канал")
    @app_commands.describe(author="Участник (необязательно, для поиска по /quote user)")
    @app_commands.guild_only()
    async def quote_import(
        self,
        interaction: discord.Interaction,
        author: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_modal(
            QuoteComposeModal(self.bot, author=author, silent=True),
        )

    @quote.command(name="edit", description="Изменить цитату по номеру")
    @app_commands.describe(
        quote_id="Номер цитаты (см. /quote list или footer у цитаты)",
        author="Новый автор для поиска (необязательно)",
    )
    @app_commands.guild_only()
    async def quote_edit(
        self,
        interaction: discord.Interaction,
        quote_id: int,
        author: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        quote = await self.bot.quote_service.get(interaction.guild.id, quote_id)
        if quote is None:
            await interaction.response.send_message(
                embed=error_embed("Цитата не найдена"),
                ephemeral=True,
            )
            return
        if not self.bot.quote_service.can_manage(quote, interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Нет прав изменить эту цитату"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(QuoteEditModal(self.bot, quote, author))

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
                interaction.guild,
                interaction.guild.id,
                quote_id,
                interaction.user,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"Цитата #{quote_id} удалена, номера обновлены"),
            ephemeral=True,
        )

    @quote.command(name="cleanup", description="Почистить канал цитат и переназначить номера")
    @app_commands.guild_only()
    async def quote_cleanup(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not config.quotes_channel_id:
            await interaction.response.send_message(
                embed=error_embed("Канал цитат не настроен в /config"),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(config.quotes_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Канал цитат недоступен"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        removed = await self.bot.quote_service.cleanup_channel(interaction.guild, channel)
        numbered = await self.bot.quote_service.renumber_and_sync(interaction.guild)
        self._cleaned_guilds.add(interaction.guild.id)
        await interaction.followup.send(
            embed=success_embed(
                "Очистка завершена",
                f"Удалено сообщений: **{removed}** в {channel.mention}\n"
                f"Номера переназначены: **{numbered}**, старше — меньше номер.",
            ),
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
            view=self.bot.quote_service.build_quote_card(quote, interaction.guild),
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
            preview = self.bot.quote_service.preview_text(q.content, 80)
            author = self.bot.quote_service.author_label(q, interaction.guild)
            embed.add_field(
                name=f"#{q.number}",
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
            preview = self.bot.quote_service.preview_text(q.content, 100)
            embed.add_field(name=f"#{q.number}", value=f'"{preview}"', inline=False)
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
    config = await bot.config_service.get(message.guild.id)
    in_quotes_channel = (
        config.quotes_channel_id is not None
        and message.channel.id == config.quotes_channel_id
    )
    if in_quotes_channel:
        await bot.db.set_quote_posted_message(quote.id, message.channel.id, message.id)
        await interaction.response.send_message(
            embed=success_embed("Цитата сохранена", f"#{quote.number} привязана к сообщению в канале."),
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=success_embed("Цитата сохранена"),
        ephemeral=True,
    )
    await publish_quote(bot, message.guild, quote)


@app_commands.context_menu(name="Import quote")
@app_commands.guild_only()
async def import_quote_context(
    interaction: discord.Interaction,
    message: discord.Message,
) -> None:
    bot = interaction.client
    if not isinstance(bot, ErundaBot):
        return
    if message.guild is None or message.author.bot:
        await interaction.response.send_message(
            embed=error_embed("Нельзя импортировать это сообщение"),
            ephemeral=True,
        )
        return
    try:
        quote = await bot.quote_service.add_from_message(message, interaction.user.id)
    except ValueError as exc:
        await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
        return
    config = await bot.config_service.get(message.guild.id)
    if config.quotes_channel_id and message.channel.id == config.quotes_channel_id:
        await bot.db.set_quote_posted_message(quote.id, message.channel.id, message.id)
    await interaction.response.send_message(
        embed=success_embed(
            "Цитата импортирована",
            f"#{quote.number} сохранена без публикации в канал.",
        ),
        ephemeral=True,
    )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(QuotesCog(bot))
    bot.tree.add_command(add_quote_context)
    bot.tree.add_command(import_quote_context)
