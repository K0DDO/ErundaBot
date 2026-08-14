"""Statistics listeners, /profile and /top."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import base_embed, error_embed
from bot.utils.formatting import format_duration, format_relative_span
from bot.views.top_views import TopView

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class StatisticsCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._voice_ready = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._voice_ready:
            return
        self._voice_ready = True
        try:
            await self.bot.statistics_service.recover_voice_sessions(self.bot)
            log.info("Voice sessions recovered")
        except Exception:
            log.exception("Voice session recovery failed")

    async def _stats_enabled(self, guild_id: int) -> tuple[bool, str]:
        config = await self.bot.config_service.get(guild_id)
        return config.statistics_enabled, config.timezone

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        enabled, tz_name = await self._stats_enabled(message.guild.id)
        if not enabled:
            return
        await self.bot.statistics_service.record_message(
            message.guild.id,
            message.author.id,
            message.channel.id,
            tz_name,
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, amount=1)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handle_reaction(payload, amount=-1)

    async def _handle_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        *,
        amount: int,
    ) -> None:
        if payload.guild_id is None or payload.user_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        reactor = guild.get_member(payload.user_id)
        if reactor is not None and reactor.bot:
            return

        enabled, tz_name = await self._stats_enabled(payload.guild_id)
        if not enabled:
            return

        channel = guild.get_channel(payload.channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        if message.author.bot or message.author.id == payload.user_id:
            return

        await self.bot.statistics_service.record_reaction(
            payload.guild_id,
            message.author.id,
            tz_name,
            amount=amount,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        enabled, tz_name = await self._stats_enabled(member.guild.id)
        if not enabled:
            return

        afk_id = member.guild.afk_channel.id if member.guild.afk_channel else None

        def countable(state: discord.VoiceState) -> discord.abc.Snowflake | None:
            channel = state.channel
            if channel is None:
                return None
            if afk_id is not None and channel.id == afk_id:
                return None
            return channel

        before_ch = countable(before)
        after_ch = countable(after)

        if before_ch is None and after_ch is not None:
            await self.bot.statistics_service.start_voice(
                member.guild.id,
                member.id,
                after_ch.id,
                tz_name,
            )
        elif before_ch is not None and after_ch is None:
            await self.bot.statistics_service.end_voice(
                member.guild.id,
                member.id,
                tz_name,
            )
        elif (
            before_ch is not None
            and after_ch is not None
            and before_ch.id != after_ch.id
        ):
            await self.bot.statistics_service.end_voice(
                member.guild.id,
                member.id,
                tz_name,
            )
            await self.bot.statistics_service.start_voice(
                member.guild.id,
                member.id,
                after_ch.id,
                tz_name,
            )

    @app_commands.command(name="profile", description="Профиль активности участника")
    @app_commands.describe(user="Участник (по умолчанию — вы)")
    @app_commands.guild_only()
    async def profile(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            return
        member = user or interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                embed=error_embed("Участник не найден"),
                ephemeral=True,
            )
            return

        config = await self.bot.config_service.get(interaction.guild.id)
        stats = await self.bot.statistics_service.get_user_stats(
            interaction.guild.id,
            member.id,
            config.timezone,
        )

        joined = member.joined_at
        if joined is not None:
            days = (datetime.now(timezone.utc) - joined).days
            on_server = format_relative_span(days)
        else:
            on_server = "—"

        ranks: list[str] = []
        if stats.message_rank:
            ranks.append(f"#{stats.message_rank} по сообщениям")
        if stats.voice_rank:
            ranks.append(f"#{stats.voice_rank} по voice")
        if stats.reaction_rank:
            ranks.append(f"#{stats.reaction_rank} по реакциям")
        ranks_text = "\n".join(ranks) if ranks else "пока нет"

        embed = base_embed(title=member.display_name)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Сообщения", value=str(stats.messages), inline=True)
        embed.add_field(
            name="Voice",
            value=format_duration(stats.voice_seconds),
            inline=True,
        )
        embed.add_field(
            name="Получено реакций",
            value=str(stats.reactions),
            inline=True,
        )
        embed.add_field(name="На сервере", value=on_server, inline=False)
        embed.add_field(name="Рейтинги", value=ranks_text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="top", description="Топы активности сервера")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        view = TopView(self.bot, interaction.guild.id, config.timezone)
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(StatisticsCog(bot))
