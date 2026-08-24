"""Event UI components."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.utils.formatting import role_mention

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Event

log = logging.getLogger(__name__)

_OLD_PING_EMBED_TITLES = frozenset({"Напоминание об ивенте", "Ивент начинается"})


def _italic_description(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    lines: list[str] = []
    for line in text.replace("*", "\\*").split("\n"):
        lines.append(f"*{line}*" if line.strip() else line)
    return "\n".join(lines)


def event_embed(bot: ErundaBot, event: Event, tz_name: str) -> discord.Embed:
    date_label, time_label = bot.event_service.format_starts_at(event, tz_name)
    desc_parts: list[str] = []
    if event.status == "cancelled":
        desc_parts.append("**Ивент отменён**")
    italic = _italic_description(event.description)
    if italic:
        desc_parts.append(italic)
    embed = base_embed(
        title=f"🎮 {event.title}",
        description="\n\n".join(desc_parts) or None,
    )
    embed.add_field(name="📅 Дата", value=date_label, inline=True)
    embed.add_field(name="🕘 Время", value=time_label, inline=True)
    embed.add_field(
        name="🔔 Роль",
        value=role_mention(event.ping_role_id),
        inline=True,
    )
    if event.status == "cancelled":
        embed.color = 0xED4245
        embed.set_footer(text="Ерунда")
    elif event.status == "completed":
        embed.color = 0x57F287
        embed.set_footer(text="Ерунда • завершено")
    elif event.number:
        embed.set_footer(text=f"Ерунда · #{event.number}")
    return embed


async def close_event_card(bot: ErundaBot, event: Event, tz_name: str) -> None:
    if not event.message_id:
        return
    guild = bot.get_guild(event.guild_id)
    if guild is None:
        return
    config = await bot.config_service.get(event.guild_id)
    channel_id = event.channel_id or config.events_channel_id
    channel = guild.get_channel(channel_id or 0) if channel_id else None
    if channel is None or not hasattr(channel, "fetch_message"):
        return
    try:
        msg = await channel.fetch_message(event.message_id)
        await msg.edit(embed=event_embed(bot, event, tz_name), view=None)
    except discord.HTTPException:
        pass


async def resync_event_cards(bot: ErundaBot, events: list[Event]) -> None:
    for event in events:
        if not event.message_id:
            continue
        guild = bot.get_guild(event.guild_id)
        if guild is None:
            continue
        config = await bot.config_service.get(event.guild_id)
        channel_id = event.channel_id or config.events_channel_id
        channel = guild.get_channel(channel_id or 0) if channel_id else None
        if channel is None or not hasattr(channel, "fetch_message"):
            continue
        try:
            msg = await channel.fetch_message(event.message_id)
            await msg.edit(embed=event_embed(bot, event, config.timezone), view=None)
        except discord.HTTPException:
            pass


def _is_bot_event_mention(
    message: discord.Message,
    bot_user_id: int,
    *,
    title: str | None = None,
    keep_ids: set[int] | None = None,
) -> bool:
    if keep_ids and message.id in keep_ids:
        return False
    if message.author.id != bot_user_id:
        return False
    if message.embeds:
        embed = message.embeds[0]
        if (embed.title or "") in _OLD_PING_EMBED_TITLES:
            if title is None or title in (embed.description or ""):
                return True
    content = message.content or ""
    if not content:
        return False
    lowered = content.lower()
    if "ивент" not in lowered:
        return False
    has_mention = bool(message.role_mentions or message.mentions) or "<@" in content
    if not has_mention:
        return False
    if title is not None and title not in content:
        return False
    return "идёт" in lowered or "осталось" in lowered or "сейчас" in lowered


async def cleanup_event_mentions(
    bot: ErundaBot,
    guild: discord.Guild,
    *,
    channel_id: int | None,
    title: str | None = None,
    keep_message_ids: set[int] | None = None,
    history_limit: int = 200,
) -> int:
    """Delete bot ping/reminder messages in the events channel."""
    if channel_id is None or guild.me is None:
        return 0
    channel = guild.get_channel(channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        return 0
    removed = 0
    try:
        async for message in channel.history(limit=history_limit):
            if not _is_bot_event_mention(
                message,
                guild.me.id,
                title=title,
                keep_ids=keep_message_ids,
            ):
                continue
            try:
                await message.delete()
                removed += 1
            except discord.HTTPException:
                pass
    except discord.HTTPException:
        log.warning("Failed to sweep event mentions in guild %s", guild.id)
    return removed


async def sweep_orphan_event_mentions(bot: ErundaBot) -> None:
    """Remove leftover event pings/reminders; keep pings for live events."""
    for config in await bot.db.list_guilds():
        guild = bot.get_guild(config.guild_id)
        if guild is None or guild.me is None:
            continue
        live = await bot.event_service.list_scheduled(config.guild_id)
        keep = {ev.message_id for ev in live if ev.message_id}
        live_titles = {ev.title for ev in live}
        channel_ids = {config.events_channel_id}
        channel_ids.update(ev.channel_id for ev in live if ev.channel_id)
        for channel_id in channel_ids:
            if channel_id is None:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                continue
            try:
                async for message in channel.history(limit=200):
                    if keep and message.id in keep:
                        continue
                    if message.author.id != guild.me.id:
                        continue
                    if message.embeds and (message.embeds[0].title or "") in _OLD_PING_EMBED_TITLES:
                        try:
                            await message.delete()
                        except discord.HTTPException:
                            pass
                        continue
                    if not _is_bot_event_mention(message, guild.me.id, keep_ids=keep):
                        continue
                    if any(title in (message.content or "") for title in live_titles):
                        continue
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
            except discord.HTTPException:
                log.warning("Failed to sweep orphan event mentions in guild %s", guild.id)


async def retire_event(bot: ErundaBot, event: Event, *, status: str) -> None:
    event.status = status
    config = await bot.config_service.get(event.guild_id)
    guild = bot.get_guild(event.guild_id)
    channel_id = event.channel_id or config.events_channel_id
    await close_event_card(bot, event, config.timezone)
    if guild is not None:
        keep = {event.message_id} if event.message_id else set()
        await cleanup_event_mentions(
            bot,
            guild,
            channel_id=channel_id,
            title=event.title,
            keep_message_ids=keep,
        )
    remaining = await bot.event_service.delete_and_renumber(event)
    await resync_event_cards(bot, remaining)


class EventCreateModal(discord.ui.Modal, title="Создать ивент"):
    title_input = discord.ui.TextInput(label="Название", max_length=100, required=True)
    description = discord.ui.TextInput(
        label="Описание",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=False,
    )
    date = discord.ui.TextInput(label="Дата (DD.MM.YYYY)", placeholder="15.08.2026", max_length=10)
    time = discord.ui.TextInput(label="Время (HH:MM)", placeholder="21:00", max_length=5)

    def __init__(
        self,
        bot: ErundaBot,
        guild_id: int,
        organizer_id: int,
        tz_name: str,
        ping_role_id: int,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.organizer_id = organizer_id
        self.tz_name = tz_name
        self.ping_role_id = ping_role_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            event = await self.bot.event_service.create(
                self.guild_id,
                str(self.title_input.value),
                str(self.description.value or ""),
                str(self.date.value),
                str(self.time.value),
                self.organizer_id,
                self.tz_name,
                ping_role_id=self.ping_role_id,
            )
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Ошибка", str(exc)),
                ephemeral=True,
            )
            return

        config = await self.bot.config_service.get(self.guild_id)
        channel_id = config.events_channel_id or interaction.channel_id
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                embed=error_embed("Канал ивентов не найден. Настрой /config."),
                ephemeral=True,
            )
            return

        embed = event_embed(self.bot, event, self.tz_name)
        message = await channel.send(embed=embed)
        await self.bot.event_service.set_message(event.id, message.id)
        await self.bot.db.update_event(event.id, channel_id=channel.id)

        await interaction.response.send_message(
            embed=success_embed("Ивент создан", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )
        remaining = await self.bot.db.list_events(self.guild_id, status="scheduled")
        await resync_event_cards(self.bot, remaining)


class EventCancelConfirmView(discord.ui.View):
    def __init__(self, bot: ErundaBot, event_id: int, requester_id: int) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.event_id = event_id
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
        event = await self.bot.event_service.get(self.event_id)
        if event is None:
            await interaction.response.edit_message(
                content=None,
                embed=error_embed("Ивент не найден"),
                view=None,
            )
            return
        try:
            event = await self.bot.event_service.cancel(event.id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.edit_message(
                content=None,
                embed=error_embed(str(exc)),
                view=None,
            )
            return
        await interaction.response.defer()
        await retire_event(self.bot, event, status="cancelled")
        await interaction.edit_original_response(
            content=None,
            embed=success_embed("Ивент отменён"),
            view=None,
        )

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def abort(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content=None,
            embed=success_embed("Ивент не отменён"),
            view=None,
        )
