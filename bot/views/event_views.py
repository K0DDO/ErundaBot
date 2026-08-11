"""Event UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import base_embed, error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Event


def event_embed(
    bot: ErundaBot,
    event: Event,
    tz_name: str,
    participant_count: int,
) -> discord.Embed:
    date_label, time_label = bot.event_service.format_starts_at(event, tz_name)
    limit = f"{participant_count}/{event.max_participants}" if event.max_participants else str(participant_count)
    desc = event.description or "—"
    embed = base_embed(
        title=f"🎮 {event.title}",
        description=desc,
    )
    embed.add_field(name="📅 Дата", value=date_label, inline=True)
    embed.add_field(name="🕘 Время", value=time_label, inline=True)
    embed.add_field(name="👥 Участники", value=limit, inline=True)
    embed.add_field(name="Организатор", value=f"<@{event.organizer_id}>", inline=False)
    if event.status == "cancelled":
        embed.color = 0xED4245
        embed.set_footer(text="Ерунда • отменено")
    elif event.status == "completed":
        embed.color = 0x57F287
        embed.set_footer(text="Ерунда • завершено")
    return embed


class EventCreateModal(discord.ui.Modal, title="Создать мероприятие"):
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

        count = await self.bot.event_service.participant_count(event.id)
        embed = event_embed(self.bot, event, self.tz_name, count)
        view = EventView(self.bot, event.id)
        message = await channel.send(embed=embed, view=view)
        await self.bot.event_service.set_message(event.id, message.id)
        await self.bot.db.update_event(event.id, channel_id=channel.id)
        self.bot.add_view(view, message_id=message.id)

        await interaction.response.send_message(
            embed=success_embed("Мероприятие создано", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )


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

        info_btn = discord.ui.Button(
            label="Подробнее",
            style=discord.ButtonStyle.primary,
            custom_id=f"event:info:{event_id}",
        )
        info_btn.callback = self.info_button
        self.add_item(info_btn)

    async def _refresh_message(self, interaction: discord.Interaction, event: Event) -> None:
        config = await self.bot.config_service.get(event.guild_id)
        count = await self.bot.event_service.participant_count(event.id)
        embed = event_embed(self.bot, event, config.timezone, count)
        disabled = event.status != "scheduled"
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id != f"event:info:{event.id}":
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

    async def info_button(self, interaction: discord.Interaction) -> None:
        event = await self.bot.event_service.get(self.event_id)
        if event is None:
            await interaction.response.send_message(embed=error_embed("Не найдено"), ephemeral=True)
            return
        participants = await self.bot.db.list_event_participants(event.id)
        names = ", ".join(f"<@{uid}>" for uid in participants[:20]) or "пока никого"
        config = await self.bot.config_service.get(event.guild_id)
        date_label, time_label = self.bot.event_service.format_starts_at(event, config.timezone)
        await interaction.response.send_message(
            embed=base_embed(
                title=event.title,
                description=(
                    f"{event.description or '—'}\n\n"
                    f"📅 {date_label} 🕘 {time_label}\n"
                    f"Участники: {names}"
                ),
            ),
            ephemeral=True,
        )


def register_event_views(bot: ErundaBot, events: list[Event]) -> None:
    for event in events:
        if event.message_id and event.status == "scheduled":
            bot.add_view(EventView(bot, event.id), message_id=event.message_id)
