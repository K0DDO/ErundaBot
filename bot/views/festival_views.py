"""Festival UI components."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.services.festival_service import normalize_film_title, pick_guild_emoji
from bot.utils.birthday_emojis import ensure_guild_emojis
from bot.utils.embeds import BRAND_COLOR, ERROR_COLOR, SUCCESS_COLOR, error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Festival

FEST_MENTIONS = discord.AllowedMentions(everyone=False, users=True, roles=True)
log = logging.getLogger(__name__)


def _notice(text: str, color: int) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    container = ui.Container(accent_color=color)
    container.add_item(ui.TextDisplay(f"**{text}**"))
    view.add_item(container)
    return view


async def festival_card(
    bot: ErundaBot,
    guild: discord.Guild,
    festival: Festival,
    *,
    confirm_delete_for: int | None = None,
) -> FestivalCardView:
    config = await bot.config_service.get(guild.id)
    await ensure_guild_emojis(guild)
    has_winner = bool(festival.winner_user_id and festival.winner_film)
    if has_winner:
        films = await bot.festival_service.ensure_posters(
            festival.id,
            user_id=festival.winner_user_id,
        )
    else:
        films = await bot.festival_service.films(festival.id)
    winner_emoji = pick_guild_emoji(guild, festival.id)
    ping_role = None
    if has_winner and config.fest_ping_role_id:
        ping_role = guild.get_role(config.fest_ping_role_id)
    body = bot.festival_service.card_body(
        festival,
        films,
        config.timezone,
        guild,
        winner_emoji=winner_emoji,
        ping_role=ping_role,
    )
    return FestivalCardView(
        bot,
        festival,
        body,
        films,
        confirm_delete_for=confirm_delete_for,
    )


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
        message = await channel.send(view=view, allowed_mentions=FEST_MENTIONS)
        if save:
            await bot.festival_service.set_message(festival.id, channel.id, message.id)
            if festival.status == "open":
                bot.add_view(FestivalView(bot, festival.id), message_id=message.id)
    return message


async def refresh_festival_message(
    bot: ErundaBot,
    festival: Festival,
    *,
    repost: bool = False,
) -> None:
    guild = bot.get_guild(festival.guild_id)
    if guild is None or not festival.message_id:
        return
    config = await bot.config_service.get(festival.guild_id)
    channel_id = festival.channel_id or config.fest_channel_id
    channel = guild.get_channel(channel_id or 0) if channel_id else None
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    view = await festival_card(bot, guild, festival)
    if repost and hasattr(channel, "send"):
        await delete_festival_message(bot, festival)
        message = await channel.send(view=view, allowed_mentions=FEST_MENTIONS)
        await bot.festival_service.set_message(festival.id, channel.id, message.id)
        return
    try:
        msg = await channel.fetch_message(festival.message_id)
        await msg.edit(content=None, embeds=[], view=view, allowed_mentions=FEST_MENTIONS)
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
        await interaction.followup.send(
            embed=success_embed(text, f"**{normalize_film_title(film.title)}**"),
            ephemeral=True,
        )


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
        await interaction.response.defer(ephemeral=True)
        config = await self.bot.config_service.get(self.guild_id)
        channel = None
        if config.fest_channel_id:
            found = interaction.guild.get_channel(config.fest_channel_id)
            if found is not None and hasattr(found, "send"):
                channel = found
        if channel is None and interaction.channel is not None and hasattr(interaction.channel, "send"):
            channel = interaction.channel
        if channel is None:
            await interaction.followup.send(
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
            if previous is not None:
                await refresh_festival_message(self.bot, previous)
            message = await publish_festival_message(
                self.bot, interaction.guild, festival, channel, save=True
            )
        except ValueError as extra:
            await interaction.followup.send(embed=error_embed(str(extra)), ephemeral=True)
            return
        except Exception:
            log.exception("Failed to create festival")
            await interaction.followup.send(
                embed=error_embed("Не получилось создать кинофестиваль"),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
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


class FestivalDeleteConfirmButton(ui.Button):
    def __init__(self, bot: ErundaBot, festival_id: int, requester_id: int) -> None:
        super().__init__(label="Удалить", style=discord.ButtonStyle.danger)
        self.bot = bot
        self.festival_id = festival_id
        self.requester_id = requester_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=error_embed("Это подтверждение не для тебя"),
                ephemeral=True,
            )
            return
        festival = await self.bot.db.get_festival(self.festival_id)
        if festival is None:
            await interaction.response.edit_message(view=_notice("Кинофестиваль не найден", ERROR_COLOR))
            return
        await interaction.response.defer()
        await delete_festival_message(self.bot, festival)
        remaining = await self.bot.festival_service.delete_and_renumber(festival)
        for item in remaining:
            await refresh_festival_message(self.bot, item)
        await interaction.edit_original_response(view=_notice("Кинофестиваль удалён", SUCCESS_COLOR))


class FestivalDeleteAbortButton(ui.Button):
    def __init__(self, requester_id: int) -> None:
        super().__init__(label="Отмена", style=discord.ButtonStyle.secondary)
        self.requester_id = requester_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                embed=error_embed("Это подтверждение не для тебя"),
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(view=_notice("Кинофестиваль не удалён", BRAND_COLOR))


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
        films: list,
        *,
        confirm_delete_for: int | None = None,
    ) -> None:
        super().__init__(timeout=120 if confirm_delete_for is not None else None)
        color = 0x57F287 if festival.status != "open" else 0x7C9CFF
        container = ui.Container(accent_color=color)
        if confirm_delete_for is not None:
            container.add_item(
                ui.TextDisplay(
                    "**Удалить этот кинофестиваль?**\nКарточка и заявки пропадут."
                )
            )
        container.add_item(ui.TextDisplay(f"## 🎬 Кинофестиваль #{festival.number}\n\n{body}"))
        if festival.winner_user_id:
            winner = next((film for film in films if film.user_id == festival.winner_user_id), None)
            if winner is not None and winner.image_url:
                container.add_item(
                    ui.MediaGallery(
                        discord.MediaGalleryItem(
                            winner.image_url,
                            description=normalize_film_title(winner.title)[:256],
                        )
                    )
                )
        if confirm_delete_for is not None:
            row = ui.ActionRow()
            row.add_item(FestivalDeleteConfirmButton(bot, festival.id, confirm_delete_for))
            row.add_item(FestivalDeleteAbortButton(confirm_delete_for))
            container.add_item(row)
        elif festival.status == "open":
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
