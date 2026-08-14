"""Config UI components."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import error_embed, success_embed
from bot.utils.formatting import bool_label, channel_mention, role_mention
from bot.utils.permissions import bot_cannot_send_reason, can_edit_config, config_denied_reason
from bot.utils.timezones import is_valid_timezone

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import GuildConfig


CHANNEL_OPTIONS = (
    ("birthday_channel_id", "Дни рождения"),
    ("events_channel_id", "Ивенты"),
    ("proposals_channel_id", "Голосования"),
    ("quotes_channel_id", "Цитаты"),
    ("fest_channel_id", "Кинофестиваль"),
    ("tgk_channel_id", "ТГК"),
)

FLAG_OPTIONS = (
    ("statistics_enabled", "Статистика"),
    ("personal_roles_enabled", "Персональные роли"),
    ("auto_execute_proposals", "Автовыполнение предложений"),
)

ROLE_OPTIONS = (
    ("config_role_id", "Доступ к /config"),
    ("fest_ping_role_id", "Пинг кинофестиваля"),
)


def config_overview_embed(config: GuildConfig) -> discord.Embed:
    embed = discord.Embed(
        title="Настройки Ерунды",
        description="Выбери раздел ниже, чтобы изменить параметры.",
        color=0x7C9CFF,
    )
    embed.add_field(
        name="Каналы",
        value=(
            f"Дни рождения: {channel_mention(config.birthday_channel_id)}\n"
            f"Ивенты: {channel_mention(config.events_channel_id)}\n"
            f"Голосования: {channel_mention(config.proposals_channel_id)}\n"
            f"Цитаты: {channel_mention(config.quotes_channel_id)}\n"
            f"Кинофестиваль: {channel_mention(config.fest_channel_id)}\n"
            f"ТГК: {channel_mention(config.tgk_channel_id)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Роли",
        value=(
            f"Доступ к /config: {role_mention(config.config_role_id)}\n"
            f"Пинг кинофестиваля: {role_mention(config.fest_ping_role_id)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Флаги",
        value=(
            f"Статистика: {bool_label(config.statistics_enabled)}\n"
            f"Персональные роли: {bool_label(config.personal_roles_enabled)}\n"
            f"Автовыполнение: {bool_label(config.auto_execute_proposals)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Время и голосования",
        value=(
            f"Timezone: `{config.timezone}`\n"
            f"Поздравления: `{config.birthday_announce_time}`\n"
            f"Напоминание ДР (дней): `{config.birthday_reminder_days}`\n"
            f"Напоминание ивента (мин): `{config.event_reminder_minutes}`\n"
            f"Напоминание кино (мин): `{config.fest_reminder_minutes}`\n"
            f"Длительность голосования (ч): `{config.proposal_duration_hours}`\n"
            f"Кворум: `{config.proposal_quorum}`\n"
            f"Порог принятия: `{config.proposal_pass_ratio:.0%}`"
        ),
        inline=False,
    )
    embed.set_footer(text="Ерунда")
    return embed


class ConfigPanel(discord.ui.View):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                embed=error_embed("Ошибка", "Неверный сервер."),
                ephemeral=True,
            )
            return False
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        config = await self.bot.config_service.get(self.guild_id)
        if not can_edit_config(member, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Недостаточно прав",
                    config_denied_reason(config.config_role_id),
                ),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.select(
        placeholder="Раздел настроек…",
        options=[
            discord.SelectOption(label="Каналы", value="channels", emoji="📺"),
            discord.SelectOption(label="Роли", value="roles", emoji="🎭"),
            discord.SelectOption(label="Флаги", value="flags", emoji="🏳️"),
            discord.SelectOption(label="Timezone", value="timezone", emoji="🌍"),
            discord.SelectOption(label="Время уведомлений", value="times", emoji="⏰"),
            discord.SelectOption(label="Голосования", value="votes", emoji="🗳️"),
        ],
    )
    async def section_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        value = select.values[0]
        if value == "channels":
            await interaction.response.send_message(
                "Сначала выбери тип канала, затем сам канал.",
                view=ChannelConfigView(self.bot, self.guild_id),
                ephemeral=True,
            )
        elif value == "roles":
            await interaction.response.send_message(
                "Сначала выбери тип роли, затем саму роль.",
                view=RoleConfigView(self.bot, self.guild_id),
                ephemeral=True,
            )
        elif value == "flags":
            await interaction.response.send_message(
                "Выбери флаг, чтобы переключить его.",
                view=FlagConfigView(self.bot, self.guild_id),
                ephemeral=True,
            )
        elif value == "timezone":
            await interaction.response.send_modal(TimezoneModal(self.bot, self.guild_id))
        elif value == "times":
            await interaction.response.send_modal(TimesModal(self.bot, self.guild_id))
        elif value == "votes":
            await interaction.response.send_modal(VotesModal(self.bot, self.guild_id))


class ChannelFieldSelect(discord.ui.Select):
    def __init__(self, parent: "ChannelConfigView") -> None:
        super().__init__(
            placeholder="Какой канал настроить?",
            options=[
                discord.SelectOption(label=label, value=field)
                for field, label in CHANNEL_OPTIONS
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_field = self.values[0]
        await interaction.response.defer(ephemeral=True)


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, parent: "ChannelConfigView") -> None:
        super().__init__(
            placeholder="Выбери текстовый канал или объявления",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.parent_view.selected_field is None:
            await interaction.response.send_message(
                embed=error_embed("Сначала выбери тип канала"),
                ephemeral=True,
            )
            return

        channel = self.values[0]
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                embed=error_embed("Только на сервере"),
                ephemeral=True,
            )
            return
        reason = bot_cannot_send_reason(guild, channel)
        if reason:
            await interaction.response.send_message(
                embed=error_embed("Нельзя выбрать этот канал", reason),
                ephemeral=True,
            )
            return

        field = self.parent_view.selected_field
        config = await self.parent_view.bot.config_service.set_channel(
            self.parent_view.guild_id,
            field,
            channel.id,
        )
        label = dict(CHANNEL_OPTIONS).get(field, field)
        await interaction.response.send_message(
            embed=success_embed(
                "Канал обновлён",
                f"{label}: {channel_mention(getattr(config, field))}",
            ),
            ephemeral=True,
        )
        if field == "birthday_channel_id" and interaction.guild is not None:
            await self.parent_view.bot.db.set_birthday_board_message_id(
                self.parent_view.guild_id, None
            )
            from bot.views.birthday_views import refresh_birthday_board

            await refresh_birthday_board(self.parent_view.bot, interaction.guild)
        if field == "tgk_channel_id" and interaction.guild is not None:
            await self.parent_view.bot.db.set_tgk_board_message_id(
                self.parent_view.guild_id, None
            )
            from bot.views.tgk_views import refresh_tgk_board

            await refresh_tgk_board(self.parent_view.bot, interaction.guild)


class ChannelConfigView(discord.ui.View):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.selected_field: str | None = None
        self.add_item(ChannelFieldSelect(self))
        self.add_item(ChannelPicker(self))


class RoleFieldSelect(discord.ui.Select):
    def __init__(self, parent: "RoleConfigView") -> None:
        super().__init__(
            placeholder="Какую роль настроить?",
            options=[
                discord.SelectOption(label=label, value=field)
                for field, label in ROLE_OPTIONS
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_field = self.values[0]
        await interaction.response.defer(ephemeral=True)


class ConfigRolePicker(discord.ui.RoleSelect):
    def __init__(self, parent: "RoleConfigView") -> None:
        super().__init__(
            placeholder="Выбери роль",
            min_values=1,
            max_values=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.parent_view.selected_field is None:
            await interaction.response.send_message(
                embed=error_embed("Сначала выбери тип роли"),
                ephemeral=True,
            )
            return
        role = self.values[0]
        field = self.parent_view.selected_field
        config = await self.parent_view.bot.config_service.set_role(
            self.parent_view.guild_id,
            field,
            role.id,
        )
        label = dict(ROLE_OPTIONS).get(field, field)
        await interaction.response.send_message(
            embed=success_embed(
                "Роль обновлена",
                f"{label}: {role_mention(getattr(config, field))}",
            ),
            ephemeral=True,
        )


class RoleConfigView(discord.ui.View):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.selected_field: str | None = None
        self.add_item(RoleFieldSelect(self))
        self.add_item(ConfigRolePicker(self))


class FlagSelect(discord.ui.Select):
    def __init__(self, parent: "FlagConfigView") -> None:
        super().__init__(
            placeholder="Какой флаг изменить?",
            options=[
                discord.SelectOption(label=f"{label} — переключить", value=field)
                for field, label in FLAG_OPTIONS
            ],
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        field = self.values[0]
        current = await self.parent_view.bot.config_service.get(self.parent_view.guild_id)
        new_value = not bool(getattr(current, field))
        config = await self.parent_view.bot.config_service.set_flag(
            self.parent_view.guild_id,
            field,
            new_value,
        )
        label = dict(FLAG_OPTIONS).get(field, field)
        await interaction.response.send_message(
            embed=success_embed(
                "Флаг обновлён",
                f"{label}: **{bool_label(getattr(config, field))}**",
            ),
            ephemeral=True,
        )


class FlagConfigView(discord.ui.View):
    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id
        self.add_item(FlagSelect(self))


class TimezoneModal(discord.ui.Modal, title="Timezone сервера"):
    timezone = discord.ui.TextInput(
        label="IANA timezone",
        placeholder="Europe/Moscow",
        required=True,
        max_length=64,
    )

    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = str(self.timezone.value).strip()
        if not is_valid_timezone(value):
            await interaction.response.send_message(
                embed=error_embed("Неизвестный timezone", f"`{value}`"),
                ephemeral=True,
            )
            return
        config = await self.bot.config_service.set_timezone(self.guild_id, value)
        await interaction.response.send_message(
            embed=success_embed("Timezone обновлён", f"`{config.timezone}`"),
            ephemeral=True,
        )


class TimesModal(discord.ui.Modal, title="Время уведомлений"):
    announce_time = discord.ui.TextInput(
        label="Время поздравлений (HH:MM)",
        placeholder="09:00",
        required=True,
        max_length=5,
    )
    reminder_days = discord.ui.TextInput(
        label="Напоминание ДР (дней заранее)",
        placeholder="1",
        required=True,
        max_length=2,
    )
    event_minutes = discord.ui.TextInput(
        label="Напоминание ивента (минут)",
        placeholder="60",
        required=True,
        max_length=5,
    )
    fest_minutes = discord.ui.TextInput(
        label="Напоминание кино (минут, 0 = выкл)",
        placeholder="60",
        required=True,
        max_length=5,
    )

    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.config_service.set_birthday_announce_time(
                self.guild_id,
                str(self.announce_time.value).strip(),
            )
            await self.bot.config_service.set_int(
                self.guild_id,
                "birthday_reminder_days",
                int(str(self.reminder_days.value).strip()),
            )
            await self.bot.config_service.set_int(
                self.guild_id,
                "event_reminder_minutes",
                int(str(self.event_minutes.value).strip()),
            )
            config = await self.bot.config_service.set_int(
                self.guild_id,
                "fest_reminder_minutes",
                int(str(self.fest_minutes.value).strip()),
            )
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Ошибка", str(exc)),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=success_embed(
                "Время обновлено",
                (
                    f"Поздравления: `{config.birthday_announce_time}`\n"
                    f"ДР заранее: `{config.birthday_reminder_days}` дн.\n"
                    f"Ивент: `{config.event_reminder_minutes}` мин.\n"
                    f"Кино: `{config.fest_reminder_minutes}` мин."
                ),
            ),
            ephemeral=True,
        )


class VotesModal(discord.ui.Modal, title="Правила голосований"):
    duration = discord.ui.TextInput(
        label="Длительность (часов)",
        placeholder="24",
        required=True,
        max_length=4,
    )
    quorum = discord.ui.TextInput(
        label="Кворум (минимум голосов)",
        placeholder="3",
        required=True,
        max_length=4,
    )
    ratio = discord.ui.TextInput(
        label="Порог принятия (0.5–1.0)",
        placeholder="0.5",
        required=True,
        max_length=4,
    )

    def __init__(self, bot: ErundaBot, guild_id: int) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self.bot.config_service.set_int(
                self.guild_id,
                "proposal_duration_hours",
                int(str(self.duration.value).strip()),
            )
            await self.bot.config_service.set_int(
                self.guild_id,
                "proposal_quorum",
                int(str(self.quorum.value).strip()),
            )
            config = await self.bot.config_service.set_pass_ratio(
                self.guild_id,
                float(str(self.ratio.value).strip().replace(",", ".")),
            )
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Ошибка", str(exc)),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=success_embed(
                "Правила голосований обновлены",
                (
                    f"Длительность: `{config.proposal_duration_hours}` ч\n"
                    f"Кворум: `{config.proposal_quorum}`\n"
                    f"Порог: `{config.proposal_pass_ratio:.0%}`"
                ),
            ),
            ephemeral=True,
        )
