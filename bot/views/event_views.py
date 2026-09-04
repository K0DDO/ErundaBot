"""Event UI components."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.utils.embeds import (
    BRAND_COLOR,
    ERROR_COLOR,
    SUCCESS_COLOR,
    base_embed,
    error_embed,
    success_embed,
)
from bot.utils.formatting import role_mention
from bot.utils.permissions import bot_cannot_send_reason

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Event

log = logging.getLogger(__name__)

EVENT_MENTIONS = discord.AllowedMentions(everyone=False, users=False, roles=True)
_OLD_PING_EMBED_TITLES = frozenset({"Напоминание об ивенте", "Ивент начинается"})


def _italic_description(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    lines: list[str] = []
    for line in text.replace("*", "\\*").split("\n"):
        lines.append(f"*{line}*" if line.strip() else line)
    return "\n".join(lines)


def _participant_field(event: Event, names: list[str]) -> tuple[str, str]:
    count = len(names)
    if event.max_participants:
        field_name = f"👥 Участники · {count}/{event.max_participants}"
    else:
        field_name = f"👥 Участники · {count}"
    shown = names[:25]
    lines = list(shown)
    extra = count - len(shown)
    if extra > 0:
        lines.append(f"+{extra}")
    value = "\n".join(lines) if lines else "пока никого"
    return field_name, value[:1024]


def _member_nick(member: discord.Member) -> str:
    if member.nick:
        return member.nick
    return member.global_name or member.name


async def resolve_participant_names(
    bot: ErundaBot,
    guild: discord.Guild,
    user_ids: list[int],
) -> list[str]:
    names: list[str] = []
    for user_id in user_ids:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                member = None
        if member is not None:
            names.append(_member_nick(member))
            continue
        cached = bot.get_user(user_id)
        if cached is not None:
            names.append(cached.global_name or cached.name)
            continue
        try:
            user = await bot.fetch_user(user_id)
            names.append(user.global_name or user.name)
        except discord.HTTPException:
            names.append(f"ID {user_id}")
    return names


def _event_card_color(event: Event) -> int:
    if event.status == "cancelled":
        return ERROR_COLOR
    if event.status == "completed":
        return SUCCESS_COLOR
    return BRAND_COLOR


def _event_card_text_lines(
    bot: ErundaBot,
    event: Event,
    tz_name: str,
    participant_names: list[str],
) -> list[str]:
    lines = [f"## 🎮 {event.title}"]
    if event.status == "cancelled":
        lines.append("**Ивент отменён**")
    italic = _italic_description(event.description)
    if italic:
        lines.append(italic)
    date_label, _time_label = bot.event_service.format_starts_at(event, tz_name)
    time_label = bot.event_service.time_display(event, tz_name)
    lines.append(
        f"📅 **{date_label}** · 🕘 **{time_label}** · 🔔 {role_mention(event.ping_role_id)}"
    )
    field_name, field_value = _participant_field(event, participant_names)
    lines.append(f"**{field_name}**\n{field_value}")
    if event.status == "completed":
        footer = "Ерунда · завершено"
    elif event.number:
        footer = f"Ерунда · #{event.number}"
    else:
        footer = "Ерунда"
    lines.append(f"-# {footer}")
    return lines


class EventCardView(ui.LayoutView):
    def __init__(
        self,
        bot: ErundaBot,
        event_id: int,
        text_lines: list[str],
        accent_color: int,
        *,
        show_buttons: bool,
    ) -> None:
        super().__init__(timeout=None)
        container = ui.Container(accent_color=accent_color)
        for line in text_lines:
            container.add_item(ui.TextDisplay(line))
        if show_buttons:
            row = ui.ActionRow()
            row.add_item(EventJoinButton(bot, event_id))
            row.add_item(EventLeaveButton(bot, event_id))
            container.add_item(row)
        self.add_item(container)


class EventJoinButton(ui.Button):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(
            label="Присоединиться",
            style=discord.ButtonStyle.success,
            custom_id=f"event:join:{event_id}",
        )
        self.bot = bot
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            event, _ = await self.bot.event_service.join(self.event_id, interaction.user.id)
        except ValueError as exc:
            if "не найден" in str(exc).casefold():
                await _delete_missing_event_message(interaction, str(exc))
                return
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await _refresh_event_card_interaction(self.bot, interaction, event)


class EventLeaveButton(ui.Button):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(
            label="Покинуть ивент",
            style=discord.ButtonStyle.secondary,
            custom_id=f"event:leave:{event_id}",
        )
        self.bot = bot
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            event, _ = await self.bot.event_service.leave(self.event_id, interaction.user.id)
        except ValueError as exc:
            if "не найден" in str(exc).casefold():
                await _delete_missing_event_message(interaction, str(exc))
                return
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await _refresh_event_card_interaction(self.bot, interaction, event)


async def _delete_missing_event_message(
    interaction: discord.Interaction,
    reason: str,
) -> None:
    await interaction.response.send_message(embed=error_embed(reason), ephemeral=True)
    if interaction.message is None:
        return
    try:
        await interaction.message.delete()
    except discord.HTTPException:
        pass


class EventActionView(discord.ui.View):
    """Persistent handler for join/leave buttons on V2 cards."""

    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(EventJoinButton(bot, event_id))
        self.add_item(EventLeaveButton(bot, event_id))


async def build_event_card_view(
    bot: ErundaBot,
    event: Event,
    tz_name: str,
    *,
    with_buttons: bool = True,
) -> EventCardView:
    guild = bot.get_guild(event.guild_id)
    participant_ids = await bot.event_service.participants_for_display(event)
    if guild is None:
        names = [str(uid) for uid in participant_ids]
    else:
        names = await resolve_participant_names(bot, guild, participant_ids)
    lines = _event_card_text_lines(bot, event, tz_name, names)
    show_buttons = with_buttons and event.status == "scheduled"
    return EventCardView(
        bot,
        event.id,
        lines,
        _event_card_color(event),
        show_buttons=show_buttons,
    )


async def _refresh_event_card_interaction(
    bot: ErundaBot,
    interaction: discord.Interaction,
    event: Event,
) -> None:
    config = await bot.config_service.get(event.guild_id)
    view = await build_event_card_view(bot, event, config.timezone)
    await interaction.response.edit_message(
        content=None,
        embeds=[],
        view=view,
        allowed_mentions=EVENT_MENTIONS,
    )


async def _edit_event_message(msg: discord.Message, view: ui.LayoutView) -> None:
    # LayoutView = Components V2: Discord rejects messages that also have content/embeds.
    await msg.edit(
        content=None,
        embeds=[],
        view=view,
        allowed_mentions=EVENT_MENTIONS,
    )


def event_embed(
    bot: ErundaBot,
    event: Event,
    tz_name: str,
    participant_names: list[str],
) -> discord.Embed:
    date_label, _time_label = bot.event_service.format_starts_at(event, tz_name)
    desc_parts: list[str] = []
    if event.status == "cancelled":
        desc_parts.append("**Ивент отменён**")
    italic = _italic_description(event.description)
    if italic:
        desc_parts.append(italic)
    embed = base_embed(
        title=f"🎮 {event.title}",
        description="\n\n".join(desc_parts) or None,
        color=_event_card_color(event),
    )
    embed.add_field(name="📅 Дата", value=date_label, inline=True)
    embed.add_field(
        name="🕘 Время",
        value=bot.event_service.time_display(event, tz_name),
        inline=True,
    )
    embed.add_field(
        name="🔔 Роль",
        value=role_mention(event.ping_role_id),
        inline=True,
    )
    field_name, field_value = _participant_field(event, participant_names)
    embed.add_field(name=field_name, value=field_value, inline=False)
    if event.status == "completed":
        embed.set_footer(text="Ерунда • завершено")
    elif event.number:
        embed.set_footer(text=f"Ерунда · #{event.number}")
    return embed


async def render_event_embed(bot: ErundaBot, event: Event, tz_name: str) -> discord.Embed:
    guild = bot.get_guild(event.guild_id)
    participant_ids = await bot.event_service.participants_for_display(event)
    if guild is None:
        return event_embed(bot, event, tz_name, [str(uid) for uid in participant_ids])
    names = await resolve_participant_names(bot, guild, participant_ids)
    return event_embed(bot, event, tz_name, names)


async def refresh_event_card(bot: ErundaBot, event: Event) -> None:
    if not event.message_id or event.status != "scheduled":
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
        view = await build_event_card_view(bot, event, config.timezone)
        await _edit_event_message(msg, view)
    except discord.HTTPException:
        pass


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
        view = await build_event_card_view(bot, event, tz_name, with_buttons=False)
        await _edit_event_message(msg, view)
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
            with_buttons = event.status == "scheduled"
            view = await build_event_card_view(
                bot,
                event,
                config.timezone,
                with_buttons=with_buttons,
            )
            await _edit_event_message(msg, view)
            if with_buttons:
                bind_event_view(bot, event, event.message_id)
        except discord.HTTPException:
            pass


def _component_text_blob(message: discord.Message) -> str:
    parts: list[str] = []

    def walk(items: list) -> None:
        for item in items:
            content = getattr(item, "content", None)
            if isinstance(content, str) and content:
                parts.append(content)
            children = getattr(item, "children", None) or getattr(item, "components", None)
            if children:
                walk(list(children))

    walk(list(message.components or []))
    return "\n".join(parts)


def _is_event_card_message(message: discord.Message, bot_user_id: int) -> bool:
    if message.author.id != bot_user_id:
        return False
    if message.embeds:
        title = message.embeds[0].title or ""
        if title.startswith("🎮") or title in _OLD_PING_EMBED_TITLES:
            return True
    blob = _component_text_blob(message)
    if "🎮" in blob or "Участники" in blob:
        return True
    if blob and "## 🎮" in blob:
        return True
    return False


def _is_bare_role_ping(message: discord.Message, bot_user_id: int) -> bool:
    if message.author.id != bot_user_id:
        return False
    if message.embeds or message.components:
        return False
    content = (message.content or "").strip()
    return bool(re.fullmatch(r"<@&\d+>", content))


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
    return "осталось" in lowered or "начинается" in lowered or "идёт" in lowered


def _is_orphan_event_message(
    message: discord.Message,
    bot_user_id: int,
    *,
    keep_ids: set[int],
    live_titles: set[str],
) -> bool:
    if message.id in keep_ids:
        return False
    if message.author.id != bot_user_id:
        return False
    if _is_event_card_message(message, bot_user_id):
        return True
    if _is_bare_role_ping(message, bot_user_id):
        return True
    if _is_bot_event_mention(message, bot_user_id, keep_ids=keep_ids):
        if live_titles and any(title in (message.content or "") for title in live_titles):
            return False
        return True
    return False


async def cleanup_event_mentions(
    bot: ErundaBot,
    guild: discord.Guild,
    *,
    channel_id: int | None,
    title: str | None = None,
    keep_message_ids: set[int] | None = None,
    history_limit: int = 200,
) -> int:
    """Delete bot ping messages in the events channel."""
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
    """Remove phantom event cards/pings and DB rows without a live card."""
    for config in await bot.db.list_guilds():
        guild = bot.get_guild(config.guild_id)
        if guild is None or guild.me is None:
            continue

        live = await bot.event_service.list_scheduled(config.guild_id)
        kept: list = []
        for event in live:
            if not event.message_id:
                try:
                    await bot.event_service.delete_and_renumber(event)
                    log.info("Removed phantom event %s (no message)", event.id)
                except Exception:
                    log.exception("Failed to delete phantom event %s", event.id)
                continue
            channel_id = event.channel_id or config.events_channel_id
            channel = guild.get_channel(channel_id or 0) if channel_id else None
            if channel is None or not hasattr(channel, "fetch_message"):
                kept.append(event)
                continue
            try:
                await channel.fetch_message(event.message_id)
                kept.append(event)
            except discord.NotFound:
                try:
                    await bot.event_service.delete_and_renumber(event)
                    log.info("Removed phantom event %s (missing card)", event.id)
                except Exception:
                    log.exception("Failed to delete phantom event %s", event.id)
            except discord.HTTPException:
                kept.append(event)

        keep = {ev.message_id for ev in kept if ev.message_id}
        live_titles = {ev.title for ev in kept}
        channel_ids = {config.events_channel_id}
        channel_ids.update(ev.channel_id for ev in kept if ev.channel_id)
        for channel_id in channel_ids:
            if channel_id is None:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                continue
            try:
                async for message in channel.history(limit=300):
                    if not _is_orphan_event_message(
                        message,
                        guild.me.id,
                        keep_ids=keep,
                        live_titles=live_titles,
                    ):
                        continue
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
            except discord.HTTPException:
                log.warning("Failed to sweep orphan event messages in guild %s", guild.id)


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
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)

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
            await interaction.followup.send(
                embed=error_embed("Ошибка", str(exc)),
                ephemeral=True,
            )
            return

        config = await self.bot.config_service.get(self.guild_id)
        channel_id = config.events_channel_id or interaction.channel_id
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if channel is None or not hasattr(channel, "send"):
            await self.bot.event_service.delete_and_renumber(event)
            await interaction.followup.send(
                embed=error_embed("Канал ивентов не найден. Настрой /config."),
                ephemeral=True,
            )
            return

        denied = bot_cannot_send_reason(interaction.guild, channel)
        if denied:
            await self.bot.event_service.delete_and_renumber(event)
            await interaction.followup.send(embed=error_embed(denied), ephemeral=True)
            return

        role = interaction.guild.get_role(self.ping_role_id)
        try:
            view = await build_event_card_view(self.bot, event, self.tz_name)
            # LayoutView is Components V2 — cannot combine with message content.
            message = await channel.send(view=view, allowed_mentions=EVENT_MENTIONS)
            if role is not None:
                try:
                    await channel.send(role.mention, allowed_mentions=EVENT_MENTIONS)
                except discord.HTTPException:
                    log.warning("Failed to ping role %s for event %s", role.id, event.id)
            await self.bot.event_service.set_message(event.id, message.id)
            await self.bot.db.update_event(event.id, channel_id=channel.id)
            bind_event_view(self.bot, event, message.id)
        except Exception:
            log.exception("Failed to publish event card %s", event.id)
            try:
                await self.bot.event_service.delete_and_renumber(event)
            except Exception:
                log.exception("Failed to roll back phantom event %s", event.id)
            await interaction.followup.send(
                embed=error_embed(
                    "Карточку отправить не удалось. Проверь права бота в канале ивентов."
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=success_embed("Ивент создан", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )
        try:
            await sweep_orphan_event_mentions(self.bot)
            remaining = await self.bot.db.list_events(self.guild_id, status="scheduled")
            await resync_event_cards(self.bot, remaining)
        except Exception:
            log.exception("Failed to resync event cards after create")


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


def bind_event_view(bot: ErundaBot, event: Event, message_id: int) -> None:
    if event.status == "scheduled":
        bot.add_view(EventActionView(bot, event.id), message_id=message_id)


def register_event_views(bot: ErundaBot, events: list[Event]) -> None:
    for event in events:
        if event.message_id and event.status == "scheduled":
            bind_event_view(bot, event, event.message_id)
