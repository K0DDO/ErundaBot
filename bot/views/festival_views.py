"""Festival UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.services.festival_service import normalize_film_title
from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Festival


async def festival_card(
    bot: ErundaBot,
    guild: discord.Guild,
    festival: Festival,
) -> FestivalCardView:
    films = await bot.festival_service.films(festival.id)
    body = bot.festival_service.card_body(festival, films, guild)
    posters = bot.festival_service.poster_urls(festival, films)
    return FestivalCardView(bot, festival, body, posters)


async def publish_festival_message(
    bot: ErundaBot,
    guild: discord.Guild,
    festival: Festival,
    channel,
    *,
    save: bool = True,
) -> discord.Message:
    view = await festival_card(bot, guild, festival)
    message = None
    if save and festival.message_id and festival.channel_id == getattr(channel, "id", None):
        try:
            message = await channel.fetch_message(festival.message_id)
            await message.edit(content=None, embeds=[], view=view)
        except discord.HTTPException:
            message = None
    if message is None:
        message = await channel.send(view=view)
        if save:
            await bot.festival_service.set_message(festival.id, channel.id, message.id)
            if festival.status == "open":
                bot.add_view(FestivalView(bot, festival.id), message_id=message.id)
    return message


async def refresh_festival_message(bot: ErundaBot, festival: Festival) -> None:
    guild = bot.get_guild(festival.guild_id)
    if guild is None or not festival.message_id:
        return
    config = await bot.config_service.get(festival.guild_id)
    channel_id = festival.channel_id or config.fest_channel_id
    channel = guild.get_channel(channel_id or 0) if channel_id else None
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    view = await festival_card(bot, guild, festival)
    try:
        msg = await channel.fetch_message(festival.message_id)
        await msg.edit(content=None, embeds=[], view=view)
    except discord.HTTPException:
        pass


async def delete_festival_message(bot: ErundaBot, festival: Festival) -> None:
    guild = bot.get_guild(festival.guild_id)
    if guild is None or not festival.message_id:
        return
    config = await bot.config_service.get(festival.guild_id)
    channel_id = festival.channel_id or config.fest_channel_id
    channel = guild.get_channel(channel_id or 0) if channel_id else None
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    try:
        msg = await channel.fetch_message(festival.message_id)
        await msg.delete()
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
        await interaction.response.defer(ephemeral=True)
        try:
            festival, film, replaced = await self.bot.festival_service.add_film(
                self.guild_id,
                self.user_id,
                str(self.title_input.value),
            )
        except ValueError as extra:
            await interaction.followup.send(embed=error_embed(str(extra)), ephemeral=True)
            return
        await refresh_festival_message(self.bot, festival)
        text = "Фильм заменён" if replaced else "Фильм предложен"
        extra = f"**{normalize_film_title(film.title)}**"
        if film.image_url:
            extra += "\nПостер найден."
        await interaction.followup.send(embed=success_embed(text, extra), ephemeral=True)


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
        channel = None
        if config.fest_channel_id:
            found = interaction.guild.get_channel(config.fest_channel_id)
            if found is not None and hasattr(found, "send"):
                channel = found
        if channel is None and interaction.channel is not None and hasattr(interaction.channel, "send"):
            channel = interaction.channel
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed("Некуда отправить карточку кинофестиваля"),
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
        except ValueError as extra:
            await interaction.response.send_message(embed=error_embed(str(extra)), ephemeral=True)
            return
        if previous is not None:
            await refresh_festival_message(self.bot, previous)
        message = await publish_festival_message(
            self.bot, interaction.guild, festival, channel, save=True
        )
        await interaction.response.send_message(
            embed=success_embed("Кинофестиваль создан", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )


class FestivalEditModal(discord.ui.Modal, title="Изменить кинофестиваль"):
    date = discord.ui.TextInput(label="Дата сеанса (DD.MM.YYYY)", placeholder="15.08.2026", max_length=10)
    time = discord.ui.TextInput(label="Время (HH:MM)", placeholder="21:00", max_length=5)

    def __init__(self, bot: ErundaBot, guild_id: int, tz_name: str, date_value: str, time_value: str) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.tz_name = tz_name
        self.date.default = date_value
        self.time.default = time_value

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            festival = await self.bot.festival_service.update_starts(
                self.guild_id,
                str(self.date.value),
                str(self.time.value),
                self.tz_name,
            )
        except ValueError as extra:
            await interaction.response.send_message(embed=error_embed(str(extra)), ephemeral=True)
            return
        await refresh_festival_message(self.bot, festival)
        await interaction.response.send_message(
            embed=success_embed("Кинофестиваль обновлён"),
            ephemeral=True,
        )


class FestivalDeleteConfirmView(discord.ui.View):
    def __init__(self, bot: ErundaBot, festival_id: int, requester_id: int) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.festival_id = festival_id
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=error_embed("Это подтверждение не для тебя"),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        festival = await self.bot.db.get_festival(self.festival_id)
        if festival is None:
            await interaction.response.edit_message(
                content=None,
                embed=error_embed("Кинофестиваль не найден"),
                view=None,
            )
            return
        await interaction.response.defer()
        await delete_festival_message(self.bot, festival)
        await self.bot.db.delete_festival(festival.id)
        await interaction.edit_original_response(
            content=None,
            embed=success_embed("Кинофестиваль удалён"),
            view=None,
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content=None,
            embed=success_embed("Кинофестиваль не удалён"),
            view=None,
        )


class FestivalAddButton(ui.Button):
    def __init__(self, bot: ErundaBot, festival_id: int) -> None:
        super().__init__(
            label="Предложить фильм",
            style=discord.ButtonStyle.success,
            custom_id=f"fest:add:{festival_id}",
        )
        self.bot = bot
        self.festival_id = festival_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            FestivalAddModal(self.bot, interaction.guild_id or 0, interaction.user.id)
        )


class FestivalCardView(ui.LayoutView):
    def __init__(
        self,
        bot: ErundaBot,
        festival: Festival,
        body: str,
        posters: list[tuple[str, str]],
    ) -> None:
        super().__init__(timeout=None)
        color = 0x57F287 if festival.status != "open" else 0x7C9CFF
        container = ui.Container(accent_color=color)
        container.add_item(ui.TextDisplay(f"## 🎬 Кинофестиваль #{festival.number}\n\n{body}"))
        if posters:
            gallery = ui.MediaGallery()
            for url, title in posters:
                gallery.add_item(media=url, description=title[:256])
            container.add_item(gallery)
        if festival.status == "open":
            row = ui.ActionRow()
            row.add_item(FestivalAddButton(bot, festival.id))
            container.add_item(row)
        self.add_item(container)


class FestivalView(discord.ui.View):
    def __init__(self, bot: ErundaBot, festival_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.festival_id = festival_id
        self.add_item(FestivalAddButton(bot, festival_id))


def register_festival_views(bot: ErundaBot, festivals: list[Festival]) -> None:
    for festival in festivals:
        if festival.message_id and festival.status == "open":
            bot.add_view(FestivalView(bot, festival.id), message_id=festival.message_id)
