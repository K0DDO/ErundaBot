"""Secret Santa card UI (Components V2)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.services.santa_service import SANTA_CARD_FILENAME, SANTA_COLOR
from bot.utils.embeds import error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import SantaEntry, SantaEvent

log = logging.getLogger(__name__)


def bind_santa_view(bot: ErundaBot, event: SantaEvent, message_id: int) -> None:
    if not event.drawn:
        bot.add_view(SantaPersistentView(bot, event.id), message_id=message_id)


async def register_santa_views(bot: ErundaBot) -> None:
    for event in await bot.db.list_open_santa_events():
        if event.message_id:
            bind_santa_view(bot, event, event.message_id)


async def publish_santa_message(
    bot: ErundaBot,
    guild: discord.Guild,
    event: SantaEvent,
    channel: discord.abc.Messageable,
    *,
    save: bool = True,
) -> discord.Message:
    view = await santa_card(bot, guild, event)
    message = await channel.send(view=view, file=bot.santa_service.card_file())
    if save:
        await bot.db.set_santa_event_message(event.id, message.channel.id, message.id)
        event.channel_id = message.channel.id
        event.message_id = message.id
        bind_santa_view(bot, event, message.id)
    return message


async def refresh_santa_message(bot: ErundaBot, event: SantaEvent) -> None:
    if event.channel_id is None or event.message_id is None:
        return
    guild = bot.get_guild(event.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(event.channel_id)
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    try:
        message = await channel.fetch_message(event.message_id)
    except discord.HTTPException:
        return
    view = await santa_card(bot, guild, event)
    try:
        await message.edit(
            view=view,
            attachments=[bot.santa_service.card_file()],
        )
    except discord.HTTPException:
        log.exception("Failed to refresh santa card %s", event.id)
    if not event.drawn:
        bind_santa_view(bot, event, event.message_id)


async def santa_card(
    bot: ErundaBot,
    guild: discord.Guild,
    event: SantaEvent,
) -> SantaCardView:
    lines = await bot.santa_service.participant_lines(guild, event.id)
    return SantaCardView(bot, event, lines)


class SantaFormModal(discord.ui.Modal, title="Тайный Санта"):
    address = discord.ui.TextInput(
        label="Адрес или ориентир с ПВЗ",
        style=discord.TextStyle.paragraph,
        placeholder="Город, улица / ближайший ПВЗ куда можно дойти",
        max_length=500,
        required=True,
    )
    wishlist = discord.ui.TextInput(
        label="Ссылка на вишлист",
        style=discord.TextStyle.short,
        placeholder="https://…",
        max_length=300,
        required=True,
    )

    def __init__(
        self,
        bot: ErundaBot,
        event_id: int,
        entry: SantaEntry | None = None,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.event_id = event_id
        if entry is not None:
            self.address.default = entry.address[:500]
            self.wishlist.default = entry.wishlist_url[:300]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.santa_service.upsert_entry(
                self.event_id,
                interaction.user.id,
                str(self.address.value),
                str(self.wishlist.value),
            )
            event = await self.bot.db.get_santa_event(self.event_id)
            if event is not None:
                await refresh_santa_message(self.bot, event)
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed("Заявка сохранена"),
            ephemeral=True,
        )


class SantaJoinButton(ui.Button):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(
            label="Присоединиться",
            style=discord.ButtonStyle.success,
            custom_id=f"santa:join:{event_id}",
        )
        self.bot = bot
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction) -> None:
        event = await self.bot.db.get_santa_event(self.event_id)
        if event is None:
            await interaction.response.send_message(
                embed=error_embed("Розыгрыш не найден"),
                ephemeral=True,
            )
            return
        if event.drawn:
            await interaction.response.send_message(
                embed=error_embed("Розыгрыш уже прошёл"),
                ephemeral=True,
            )
            return
        existing = await self.bot.db.get_santa_entry(self.event_id, interaction.user.id)
        if existing is not None:
            await interaction.response.send_message(
                embed=error_embed("Ты уже в списке — нажми «Изменить»"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SantaFormModal(self.bot, self.event_id))


class SantaEditButton(ui.Button):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(
            label="Изменить",
            style=discord.ButtonStyle.primary,
            custom_id=f"santa:edit:{event_id}",
        )
        self.bot = bot
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction) -> None:
        event = await self.bot.db.get_santa_event(self.event_id)
        if event is None:
            await interaction.response.send_message(
                embed=error_embed("Розыгрыш не найден"),
                ephemeral=True,
            )
            return
        if event.drawn:
            await interaction.response.send_message(
                embed=error_embed("Розыгрыш уже прошёл — менять нельзя"),
                ephemeral=True,
            )
            return
        entry = await self.bot.db.get_santa_entry(self.event_id, interaction.user.id)
        if entry is None:
            await interaction.response.send_message(
                embed=error_embed("Сначала нажми «Присоединиться»"),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            SantaFormModal(self.bot, self.event_id, entry)
        )


class SantaLeaveButton(ui.Button):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(
            label="Уйти",
            style=discord.ButtonStyle.secondary,
            custom_id=f"santa:leave:{event_id}",
        )
        self.bot = bot
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.santa_service.leave(self.event_id, interaction.user.id)
            event = await self.bot.db.get_santa_event(self.event_id)
            if event is not None:
                await refresh_santa_message(self.bot, event)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed(str(exc)),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed("Ты вышел из тайного санты"),
            ephemeral=True,
        )


class SantaCardView(ui.LayoutView):
    def __init__(
        self,
        bot: ErundaBot,
        event: SantaEvent,
        participant_lines: list[str],
    ) -> None:
        super().__init__(timeout=None)
        container = ui.Container(accent_color=SANTA_COLOR)
        status = "розыгрыш проведён" if event.drawn else "набор участников"
        container.add_item(ui.TextDisplay(f"## 🎅 Тайный Санта #{event.number}"))
        container.add_item(ui.TextDisplay(f"-# {status}"))
        if participant_lines:
            body = "**Участники:**\n" + "\n".join(participant_lines)
        else:
            body = "**Участники:**\nпока никого — жми «Присоединиться»"
        container.add_item(ui.TextDisplay(body[:3900]))
        container.add_item(
            ui.MediaGallery(
                discord.MediaGalleryItem(
                    f"attachment://{SANTA_CARD_FILENAME}",
                    description="Тайный Санта",
                )
            )
        )
        if not event.drawn:
            row = ui.ActionRow()
            row.add_item(SantaJoinButton(bot, event.id))
            row.add_item(SantaEditButton(bot, event.id))
            row.add_item(SantaLeaveButton(bot, event.id))
            container.add_item(row)
        self.add_item(container)


class SantaPersistentView(discord.ui.View):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.event_id = event_id
        self.add_item(SantaJoinButton(bot, event_id))
        self.add_item(SantaEditButton(bot, event_id))
        self.add_item(SantaLeaveButton(bot, event_id))
