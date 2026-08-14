"""Film festival slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.festival_service import normalize_film_title
from bot.utils.embeds import error_embed, success_embed
from bot.views.festival_views import (
    FestivalAddModal,
    FestivalEditModal,
    FestivalNewModal,
    festival_card,
    register_festival_views,
    refresh_festival_message,
)

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class FestivalCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._views_restored = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._views_restored:
            return
        self._views_restored = True
        try:
            festivals = await self.bot.db.list_posted_festivals()
            register_festival_views(
                self.bot,
                [item for item in festivals if item.status == "open"],
            )
            for festival in festivals:
                await refresh_festival_message(self.bot, festival)
            log.info("Restored %s festival cards", len(festivals))
        except Exception:
            log.exception("Failed to restore festival views")

    fest = app_commands.Group(name="fest", description="Кинофестиваль")

    @staticmethod
    def _post_channel(
        interaction: discord.Interaction,
        configured_id: int | None,
    ):
        guild = interaction.guild
        if guild is not None and configured_id:
            found = guild.get_channel(configured_id)
            if found is not None and hasattr(found, "send"):
                return found
        channel = interaction.channel
        if channel is not None and hasattr(channel, "send"):
            return channel
        return None

    @fest.command(name="add", description="Предложить фильм")
    @app_commands.guild_only()
    async def fest_add(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_modal(
            FestivalAddModal(self.bot, interaction.guild.id, interaction.user.id)
        )

    @fest.command(name="remove", description="Убрать свой фильм")
    @app_commands.guild_only()
    async def fest_remove(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            festival = await self.bot.festival_service.remove_film(
                interaction.guild.id,
                interaction.user.id,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_festival_message(self.bot, festival)
        await interaction.response.send_message(embed=success_embed("Фильм убран"), ephemeral=True)

    @fest.command(name="role", description="Взять / снять роль Кино (дебаг)")
    @app_commands.guild_only()
    async def fest_role(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        try:
            added = await self.bot.festival_service.toggle_staff_role(
                interaction.guild,
                interaction.user,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        text = "Роль «Кино» выдана" if added else "Роль «Кино» снята"
        await interaction.response.send_message(embed=success_embed(text), ephemeral=True)

    @fest.command(name="preview", description="Дебаг: карточка фестиваля только тебе")
    @app_commands.guild_only()
    async def fest_preview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        try:
            festival = await self.bot.festival_service.require_open(interaction.guild.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        view = await festival_card(self.bot, interaction.guild, festival)
        await interaction.response.send_message(view=view, ephemeral=True)

    @fest.command(name="test-ping", description="Дебаг: пинг до сеанса только тебе")
    @app_commands.describe(role="Кого пингануть, по умолчанию роль из прошлого ping")
    @app_commands.guild_only()
    async def fest_test_ping(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        ping_role = role
        if ping_role is None and config.fest_ping_role_id:
            ping_role = interaction.guild.get_role(config.fest_ping_role_id)
        if ping_role is None:
            await interaction.response.send_message(
                embed=error_embed("Укажи роль или сначала вызови /fest ping"),
                ephemeral=True,
            )
            return
        try:
            festival = await self.bot.festival_service.require_open(interaction.guild.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        text = self.bot.festival_service.ping_text(festival, config.timezone, ping_role)
        await interaction.response.send_message(text, ephemeral=True)

    @fest.command(name="new", description="Открыть новый кинофестиваль")
    @app_commands.guild_only()
    async def fest_new(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_modal(
            FestivalNewModal(self.bot, interaction.guild.id, config.timezone)
        )

    @fest.command(name="edit", description="Изменить дату и время сеанса")
    @app_commands.guild_only()
    async def fest_edit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
            festival = await self.bot.festival_service.require_open(interaction.guild.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        date_value, time_value = self.bot.festival_service.starts_input(
            festival, config.timezone
        )
        await interaction.response.send_modal(
            FestivalEditModal(
                self.bot,
                interaction.guild.id,
                config.timezone,
                date_value,
                time_value,
            )
        )

    @fest.command(name="delete", description="Удалить кинофестиваль")
    @app_commands.describe(number="Номер карточки, без номера — текущий открытый")
    @app_commands.guild_only()
    async def fest_delete(
        self,
        interaction: discord.Interaction,
        number: int | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
            if number is None:
                festival = await self.bot.festival_service.require_open(interaction.guild.id)
            else:
                festival = await self.bot.festival_service.require_by_number(
                    interaction.guild.id,
                    number,
                )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        view = await festival_card(
            self.bot,
            interaction.guild,
            festival,
            confirm_delete_for=interaction.user.id,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @fest_delete.autocomplete("number")
    async def fest_delete_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        festivals = await self.bot.db.list_guild_festivals(interaction.guild.id)
        choices: list[app_commands.Choice[int]] = []
        needle = current.strip()
        for festival in festivals:
            if needle and needle not in str(festival.number):
                continue
            label = f"#{festival.number}"
            if festival.status == "open":
                label += " · текущий"
            elif festival.winner_film:
                label += f" · {festival.winner_film}"
            choices.append(app_commands.Choice(name=label[:100], value=festival.number))
            if len(choices) >= 25:
                break
        return choices

    @fest.command(name="export", description="Список имён для колеса")
    @app_commands.guild_only()
    async def fest_export(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
            festival = await self.bot.festival_service.require_open(interaction.guild.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        films = await self.bot.festival_service.films(festival.id)
        names = self.bot.festival_service.export_names(films, interaction.guild)
        await interaction.response.send_message(
            embed=success_embed("Имена для колеса", f"```\n{names}\n```"),
            ephemeral=True,
        )

    @fest.command(name="winner", description="Отметить победителя колеса")
    @app_commands.describe(user="Кто выиграл в колесе")
    @app_commands.guild_only()
    async def fest_winner(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
            festival, film = await self.bot.festival_service.set_winner(
                interaction.guild.id,
                user.id,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_festival_message(self.bot, festival, repost=True)
        await interaction.response.send_message(
            embed=success_embed(
                "Победитель записан",
                f"{user.mention} — **{normalize_film_title(film.title)}**",
            ),
            ephemeral=True,
        )

    @fest.command(name="ping", description="Напомнить о сеансе")
    @app_commands.describe(role="Кого пингануть")
    @app_commands.guild_only()
    async def fest_ping(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
            festival = await self.bot.festival_service.require_open(interaction.guild.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        channel = self._post_channel(interaction, config.fest_channel_id)
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed("Некуда отправить напоминание"),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild(interaction.guild.id, fest_ping_role_id=role.id)
        text = self.bot.festival_service.ping_text(festival, config.timezone, role)
        await channel.send(text)
        await interaction.response.send_message(embed=success_embed("Напоминание отправлено"), ephemeral=True)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(FestivalCog(bot))
