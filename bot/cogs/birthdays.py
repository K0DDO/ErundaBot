"""Birthday slash commands."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.models import Birthday
from bot.services.birthday_service import format_birthday_date, member_display
from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.views.birthday_views import BirthdayPreviewView, BirthdaySetModal, refresh_birthday_board

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class BirthdaysCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._board_synced = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._board_synced:
            return
        self._board_synced = True
        for guild in self.bot.guilds:
            config = await self.bot.config_service.get(guild.id)
            if config.birthday_channel_id:
                try:
                    await self.bot.birthday_service.sync_board(guild, self.bot)
                except Exception:
                    log.exception("Failed to sync birthday board for guild %s", guild.id)

    birthday = app_commands.Group(name="birthday", description="Дни рождения")

    @birthday.command(name="set", description="Указать / изменить дату")
    @app_commands.guild_only()
    async def birthday_set(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_modal(
            BirthdaySetModal(self.bot, interaction.guild.id, interaction.user.id)
        )

    @birthday.command(name="remove", description="Удалить свой день рождения")
    @app_commands.guild_only()
    async def birthday_remove(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        removed = await self.bot.birthday_service.remove_birthday(
            interaction.guild.id,
            interaction.user.id,
        )
        if not removed:
            await interaction.response.send_message(
                embed=error_embed("День рождения не найден"),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=success_embed("День рождения удалён"),
            ephemeral=True,
        )
        await refresh_birthday_board(self.bot, interaction.guild)

    @birthday.command(name="list", description="Список дней рождения на сервере")
    @app_commands.guild_only()
    async def birthday_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        config = await self.bot.config_service.get(interaction.guild.id)
        entries = await self.bot.birthday_service.list_sorted(
            interaction.guild.id,
            config.timezone,
        )
        if not entries:
            await interaction.response.send_message(
                embed=base_embed(
                    title="Дни рождения",
                    description="Пока никто не указал день рождения.",
                ),
                ephemeral=True,
            )
            return

        description = self.bot.birthday_service.format_preview_lines(
            interaction.guild,
            entries,
            limit=25,
        )

        await interaction.response.send_message(
            embed=base_embed(
                title="Дни рождения",
                description=description,
            ),
            ephemeral=True,
        )

    @birthday.command(
        name="preview",
        description="Предпросмотр доски дней рождения (только тебе)",
    )
    @app_commands.guild_only()
    async def birthday_preview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        text = await self.bot.birthday_service.build_preview_text(
            interaction.guild,
            config,
        )
        view = BirthdayPreviewView(
            self.bot,
            interaction.guild.id,
            text,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @birthday.command(name="next", description="Ближайший день рождения")
    @app_commands.guild_only()
    async def birthday_next(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        config = await self.bot.config_service.get(interaction.guild.id)
        entry = await self.bot.birthday_service.next_birthday(
            interaction.guild.id,
            config.timezone,
        )
        if entry is None:
            await interaction.response.send_message(
                embed=base_embed(
                    title="Ближайший день рождения",
                    description="Список пуст.",
                ),
                ephemeral=True,
            )
            return

        bday = entry.birthday
        when = format_birthday_date(bday.day, bday.month)
        name = member_display(interaction.guild, bday.user_id)
        if entry.days_until == 0:
            timing = "сегодня"
        elif entry.days_until == 1:
            timing = "завтра"
        else:
            timing = f"через {entry.days_until} дн."

        await interaction.response.send_message(
            embed=base_embed(
                title="Ближайший день рождения",
                description=f"**{name}** — {when} ({timing})",
            ),
            ephemeral=True,
        )

    async def _debug_birthday_for(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> Birthday:
        existing = await self.bot.birthday_service.get_birthday(guild.id, member.id)
        if existing is not None:
            return existing
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        return Birthday(
            guild_id=guild.id,
            user_id=member.id,
            day=today.day,
            month=today.month,
            year=None,
        )

    @birthday.command(
        name="test-reminder",
        description="[тест] Напоминание о ДР без пинга (только тебе)",
    )
    @app_commands.describe(member="Участник")
    @app_commands.guild_only()
    async def birthday_test_reminder(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        birthday = await self._debug_birthday_for(interaction.guild, member)
        embed = self.bot.birthday_service.reminder_embed(
            interaction.guild,
            birthday,
            days=max(config.birthday_reminder_days, 1),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @birthday.command(
        name="test-announce",
        description="[тест] ИИ-поздравление в день ДР с пингом (только тебе)",
    )
    @app_commands.describe(member="Участник")
    @app_commands.guild_only()
    async def birthday_test_announce(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        birthday = await self._debug_birthday_for(interaction.guild, member)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        embed, _used_ai = await self.bot.birthday_service.announce_embed(
            interaction.guild,
            birthday,
            today,
            self.bot.ai_service,
            mention=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @birthday.command(
        name="test-rgb-on",
        description="[тест] Выдать RGB-роль именинника",
    )
    @app_commands.describe(member="Участник")
    @app_commands.guild_only()
    async def birthday_test_rgb_on(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.birthday_star_service.grant(
                interaction.guild,
                member,
                granted_on=date.today(),
                force=True,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.followup.send(content=result, ephemeral=True)

    @birthday.command(
        name="test-rgb-off",
        description="[тест] Снять RGB-роль именинника",
    )
    @app_commands.describe(member="Участник")
    @app_commands.guild_only()
    async def birthday_test_rgb_off(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.bot.birthday_star_service.revoke(interaction.guild, member)
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.followup.send(content=result, ephemeral=True)

    @birthday.command(
        name="test-rgb-ensure",
        description="[тест] Создать роль 🎂 Именинник, если её ещё нет",
    )
    @app_commands.guild_only()
    async def birthday_test_rgb_ensure(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            role = await self.bot.birthday_star_service.get_or_create_star_role(interaction.guild)
        except ValueError as extra:
            await interaction.followup.send(embed=error_embed(str(extra)), ephemeral=True)
            return
        await interaction.followup.send(
            embed=success_embed(
                "Роль именинника",
                f"{role.mention} готова. RGB крутится, пока роль на ком-то надета.",
            ),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
