"""Secret Santa card UI (classic embed + persistent buttons)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

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
        if event.message_id is None or event.channel_id is None:
            continue
        guild = bot.get_guild(event.guild_id)
        if guild is None:
            bind_santa_view(bot, event, event.message_id)
            continue
        channel = guild.get_channel(event.channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            bind_santa_view(bot, event, event.message_id)
            continue
        try:
            await channel.fetch_message(event.message_id)
        except discord.NotFound:
            log.warning(
                "Santa card missing in Discord (event=%s msg=%s) — republishing",
                event.id,
                event.message_id,
            )
            try:
                await publish_santa_message(bot, guild, event, channel, save=True)
            except Exception:
                log.exception("Failed to republish santa card event=%s", event.id)
            continue
        except discord.HTTPException:
            log.exception("Failed to fetch santa card event=%s", event.id)
            bind_santa_view(bot, event, event.message_id)
            continue
        bind_santa_view(bot, event, event.message_id)


def build_santa_embed(
    event: SantaEvent,
    participant_lines: list[str],
) -> discord.Embed:
    status = "розыгрыш проведён" if event.drawn else "набор участников"
    if participant_lines:
        body = "**Участники:**\n" + "\n".join(participant_lines)
    else:
        body = "**Участники:**\nпока никого — жми «Присоединиться»"
    embed = discord.Embed(
        title=f"🎅 Тайный Санта #{event.number}",
        description=f"{status}\n\n{body}"[:4090],
        color=SANTA_COLOR,
    )
    embed.set_image(url=f"attachment://{SANTA_CARD_FILENAME}")
    return embed


async def publish_santa_message(
    bot: ErundaBot,
    guild: discord.Guild,
    event: SantaEvent,
    channel: discord.abc.Messageable,
    *,
    save: bool = True,
) -> discord.Message:
    lines = await bot.santa_service.participant_lines(guild, event.id)
    embed = build_santa_embed(event, lines)
    view = None if event.drawn else SantaPersistentView(bot, event.id)
    message = await channel.send(
        embed=embed,
        file=bot.santa_service.card_file(),
        view=view,
    )
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
    lines = await bot.santa_service.participant_lines(guild, event.id)
    embed = build_santa_embed(event, lines)
    view = None if event.drawn else SantaPersistentView(bot, event.id)
    try:
        message = await channel.fetch_message(event.message_id)
    except discord.NotFound:
        log.warning(
            "Santa card gone during refresh (event=%s) — republishing",
            event.id,
        )
        try:
            await publish_santa_message(bot, guild, event, channel, save=True)
        except Exception:
            log.exception("Failed to republish santa card event=%s", event.id)
        return
    except discord.HTTPException:
        return
    try:
        await message.edit(
            content=None,
            embed=embed,
            attachments=[bot.santa_service.card_file()],
            view=view,
        )
    except discord.HTTPException:
        log.exception("Failed to refresh santa card %s", event.id)
        return
    if not event.drawn:
        bind_santa_view(bot, event, event.message_id)


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


class SantaJoinButton(discord.ui.Button):
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


class SantaEditButton(discord.ui.Button):
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


class SantaLeaveButton(discord.ui.Button):
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


class SantaPersistentView(discord.ui.View):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.event_id = event_id
        self.add_item(SantaJoinButton(bot, event_id))
        self.add_item(SantaEditButton(bot, event_id))
        self.add_item(SantaLeaveButton(bot, event_id))
