"""Film festival slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.festival_service import (
    MSK_TIMEZONE,
    film_age_rating,
    format_age_tag,
    normalize_film_title,
)
from bot.utils.embeds import error_embed, success_embed
from bot.views.festival_views import (
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
        self._presence_ready = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._views_restored:
            self._views_restored = True
            try:
                # Only re-bind persistent buttons. Do NOT edit every old card on
                # startup — that floods Discord with PATCH and 429s, and can
                # destabilize voice while the HTTP client is backed up.
                festivals = await self.bot.db.list_posted_festivals()
                register_festival_views(self.bot, festivals)
                log.info("Restored %s festival card views (no bulk refresh)", len(festivals))
            except Exception:
                log.exception("Failed to restore festival views")
        if not self._presence_ready:
            self._presence_ready = True
            try:
                await self.bot.festival_presence_service.recover(self.bot)
                log.info("Festival presence recovered")
            except Exception:
                log.exception("Festival presence recovery failed")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.guild is None:
            return
        if before.channel is None and after.channel is None:
            return
        if before.channel == after.channel and before.self_stream == after.self_stream:
            return
        try:
            await self.bot.festival_presence_service.sync_guild(member.guild)
        except Exception:
            log.exception("Festival presence sync failed for guild %s", member.guild.id)

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

    @fest.command(
        name="new",
        description="Открыть новый кинофестиваль",
    )
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
            FestivalNewModal(self.bot, interaction.guild.id, MSK_TIMEZONE)
        )

    @fest.command(
        name="edit",
        description="Изменить дату и время сеанса",
    )
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
            festival, MSK_TIMEZONE
        )
        await interaction.response.send_modal(
            FestivalEditModal(
                self.bot,
                interaction.guild.id,
                MSK_TIMEZONE,
                date_value,
                time_value,
            )
        )

    @fest.command(
        name="delete",
        description="Удалить кинофестиваль",
    )
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

    @fest.command(
        name="export",
        description="Список имён для колеса",
    )
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

    @fest.command(
        name="winner",
        description="Отметить победителя колеса",
    )
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
                f"{user.mention} — **{normalize_film_title(film.title)}**{format_age_tag(film_age_rating(film))}",
            ),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(FestivalCog(bot))
