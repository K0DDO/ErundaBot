"""Event UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import base_embed, error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Event


def _italic_description(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    lines: list[str] = []
    for line in text.replace("*", "\\*").split("\n"):
        lines.append(f"*{line}*" if line.strip() else line)
    return "\n".join(lines)


def _participant_field(event: Event, participant_ids: list[int]) -> tuple[str, str]:
    count = len(participant_ids)
    if event.max_participants:
        name = f"👥 Участники · {count}/{event.max_participants}"
    else:
        name = f"👥 Участники · {count}"
    shown = participant_ids[:25]
    lines = [f"<@{uid}>" for uid in shown]
    extra = count - len(shown)
    if extra > 0:
        lines.append(f"+{extra}")
    value = "\n".join(lines) if lines else "пока никого"
    return name, value[:1024]


def event_embed(
    bot: ErundaBot,
    event: Event,
    tz_name: str,
    participant_ids: list[int],
) -> discord.Embed:
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
    field_name, field_value = _participant_field(event, participant_ids)
    embed.add_field(name=field_name, value=field_value, inline=False)
    if event.status == "cancelled":
        embed.color = 0xED4245
        embed.set_footer(text="Ерунда")
    elif event.status == "completed":
        embed.color = 0x57F287
        embed.set_footer(text="Ерунда • завершено")
    elif event.number:
        embed.set_footer(text=f"Ерунда · #{event.number}")
    return embed


async def render_event_embed(bot: ErundaBot, event: Event, tz_name: str) -> discord.Embed:
    participants = await bot.event_service.participants_for_display(event)
    return event_embed(bot, event, tz_name, participants)


async def close_event_card(
    bot: ErundaBot,
    event: Event,
    tz_name: str,
    participant_ids: list[int],
) -> None:
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
        embed = event_embed(bot, event, tz_name, participant_ids)
        await msg.edit(embed=embed, view=None)
    except discord.HTTPException:
        pass


async def resync_event_cards(bot: ErundaBot, events: list[Event]) -> None:
    for event in events:
        await bot.event_service.ensure_organizer_participant(event)
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
            embed = await render_event_embed(bot, event, config.timezone)
            view = EventView(bot, event.id)
            await msg.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass


async def retire_event(bot: ErundaBot, event: Event, *, status: str) -> None:
    participants = await bot.event_service.participants_for_display(event)
    event.status = status
    config = await bot.config_service.get(event.guild_id)
    await close_event_card(bot, event, config.timezone, participants)
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
    max_participants = discord.ui.TextInput(
        label="Макс. участников (пусто = без лимита)",
        required=False,
        max_length=4,
    )

    def __init__(self, bot: ErundaBot, guild_id: int, organizer_id: int, tz_name: str) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.organizer_id = organizer_id
        self.tz_name = tz_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        max_raw = str(self.max_participants.value).strip() if self.max_participants.value else ""
        max_p: int | None = int(max_raw) if max_raw else None
        try:
            event = await self.bot.event_service.create(
                self.guild_id,
                str(self.title_input.value),
                str(self.description.value or ""),
                str(self.date.value),
                str(self.time.value),
                self.organizer_id,
                self.tz_name,
                max_participants=max_p,
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

        embed = await render_event_embed(self.bot, event, self.tz_name)
        view = EventView(self.bot, event.id)
        message = await channel.send(embed=embed, view=view)
        await self.bot.event_service.set_message(event.id, message.id)
        await self.bot.db.update_event(event.id, channel_id=channel.id)
        self.bot.add_view(view, message_id=message.id)

        await interaction.response.send_message(
            embed=success_embed("Ивент создан", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )
        remaining = await self.bot.db.list_events(self.guild_id, status="scheduled")
        await resync_event_cards(self.bot, remaining)


class EventView(discord.ui.View):
    def __init__(self, bot: ErundaBot, event_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.event_id = event_id

        join_btn = discord.ui.Button(
            label="Участвовать",
            style=discord.ButtonStyle.success,
            custom_id=f"event:join:{event_id}",
        )
        join_btn.callback = self.join_button
        self.add_item(join_btn)

        leave_btn = discord.ui.Button(
            label="Не участвовать",
            style=discord.ButtonStyle.secondary,
            custom_id=f"event:leave:{event_id}",
        )
        leave_btn.callback = self.leave_button
        self.add_item(leave_btn)

    async def _refresh_message(self, interaction: discord.Interaction, event: Event) -> None:
        config = await self.bot.config_service.get(event.guild_id)
        embed = await render_event_embed(self.bot, event, config.timezone)
        disabled = event.status != "scheduled"
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = disabled
        await interaction.response.edit_message(embed=embed, view=self)

    async def join_button(self, interaction: discord.Interaction) -> None:
        try:
            event, _ = await self.bot.event_service.join(self.event_id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await self._refresh_message(interaction, event)

    async def leave_button(self, interaction: discord.Interaction) -> None:
        try:
            event, _ = await self.bot.event_service.leave(self.event_id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await self._refresh_message(interaction, event)


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


def register_event_views(bot: ErundaBot, events: list[Event]) -> None:
    for event in events:
        if event.message_id and event.status == "scheduled":
            bot.add_view(EventView(bot, event.id), message_id=event.message_id)
