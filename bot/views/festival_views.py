"""Festival UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Festival


async def refresh_festival_message(bot: ErundaBot, festival: Festival) -> None:
    guild = bot.get_guild(festival.guild_id)
    if guild is None or not festival.message_id:
        return
    config = await bot.config_service.get(festival.guild_id)
    channel_id = festival.channel_id or config.fest_channel_id
    channel = guild.get_channel(channel_id or 0) if channel_id else None
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    films = await bot.festival_service.films(festival.id)
    embed = bot.festival_service.build_embed(festival, films, config.timezone, guild)
    view = FestivalView(bot, festival.id) if festival.status == "open" else None
    try:
        msg = await channel.fetch_message(festival.message_id)
        await msg.edit(embed=embed, view=view)
    except discord.HTTPException:
        pass


class FestivalAddModal(discord.ui.Modal, title="Предложить фильм"):
    title_input = discord.ui.TextInput(label="Название фильма", max_length=120, required=True)

    def __init__(self, bot: ErundaBot, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            festival, _film, replaced = await self.bot.festival_service.add_film(
                self.guild_id,
                self.user_id,
                str(self.title_input.value),
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_festival_message(self.bot, festival)
        text = "Фильм заменён" if replaced else "Фильм предложен"
        await interaction.response.send_message(embed=success_embed(text), ephemeral=True)


class FestivalNewModal(discord.ui.Modal, title="Новый кинофестиваль"):
    date = discord.ui.TextInput(label="Дата сеанса (DD.MM.YYYY)", placeholder="15.08.2026", max_length=10)
    time = discord.ui.TextInput(label="Время (HH:MM)", placeholder="21:00", max_length=5)

    def __init__(self, bot: ErundaBot, guild_id: int, tz_name: str) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.tz_name = tz_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(self.guild_id)
        if not config.fest_channel_id:
            await interaction.response.send_message(
                embed=error_embed("Канал кинофестиваля не задан в /config"),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(config.fest_channel_id)
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                embed=error_embed("Канал кинофестиваля недоступен"),
                ephemeral=True,
            )
            return
        try:
            festival, previous = await self.bot.festival_service.create(
                self.guild_id,
                str(self.date.value),
                str(self.time.value),
                self.tz_name,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        if previous is not None:
            await refresh_festival_message(self.bot, previous)
        films = await self.bot.festival_service.films(festival.id)
        embed = self.bot.festival_service.build_embed(
            festival, films, self.tz_name, interaction.guild
        )
        view = FestivalView(self.bot, festival.id)
        message = await channel.send(embed=embed, view=view)
        await self.bot.festival_service.set_message(festival.id, channel.id, message.id)
        self.bot.add_view(view, message_id=message.id)
        await interaction.response.send_message(
            embed=success_embed("Кинофестиваль создан", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )


class FestivalView(discord.ui.View):
    def __init__(self, bot: ErundaBot, festival_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.festival_id = festival_id
        add_btn = discord.ui.Button(
            label="Предложить фильм",
            style=discord.ButtonStyle.success,
            custom_id=f"fest:add:{festival_id}",
        )
        add_btn.callback = self.add_button
        self.add_item(add_btn)

    async def add_button(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            FestivalAddModal(self.bot, interaction.guild_id or 0, interaction.user.id)
        )


def register_festival_views(bot: ErundaBot, festivals: list[Festival]) -> None:
    for festival in festivals:
        if festival.message_id and festival.status == "open":
            bot.add_view(FestivalView(bot, festival.id), message_id=festival.message_id)
