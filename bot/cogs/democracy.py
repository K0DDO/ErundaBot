"""Democracy slash commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.views.proposal_views import (
    ProposalCreateModal,
    build_proposal_embed,
    register_proposal_views,
)

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class DemocracyCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._views_restored = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._views_restored:
            return
        self._views_restored = True
        try:
            open_proposals = await self.bot.db.list_open_proposals()
            register_proposal_views(self.bot, open_proposals)
            log.info("Restored %s proposal views", len(open_proposals))
        except Exception:
            log.exception("Failed to restore proposal views")

    proposal = app_commands.Group(name="proposal", description="Предложения и голосования")

    @proposal.command(name="create", description="Создать предложение")
    @app_commands.guild_only()
    async def proposal_create(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        await interaction.response.send_modal(
            ProposalCreateModal(
                self.bot,
                interaction.guild.id,
                interaction.user.id,
                config.timezone,
            )
        )

    @proposal.command(name="list", description="Список предложений")
    @app_commands.guild_only()
    async def proposal_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        proposals = await self.bot.db.list_proposals(interaction.guild.id, status="open")
        if not proposals:
            await interaction.response.send_message(
                embed=base_embed(title="Предложения", description="Нет открытых."),
            )
            return
        lines = [f"**#{p.number}** — {p.content[:80]}" for p in proposals[:15]]
        await interaction.response.send_message(
            embed=base_embed(title="Открытые предложения", description="\n".join(lines)),
        )

    @proposal.command(name="info", description="Информация о предложении")
    @app_commands.describe(number="Номер предложения")
    @app_commands.guild_only()
    async def proposal_info(self, interaction: discord.Interaction, number: int) -> None:
        if interaction.guild is None:
            return
        all_props = await self.bot.db.list_proposals(interaction.guild.id, limit=100)
        match = next((p for p in all_props if p.number == number), None)
        if match is None:
            await interaction.response.send_message(embed=error_embed("Не найдено"), ephemeral=True)
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        embed = await build_proposal_embed(self.bot, match, config.timezone, final=True)
        await interaction.response.send_message(embed=embed)

    @proposal.command(name="cancel", description="Отменить своё предложение")
    @app_commands.describe(number="Номер предложения")
    @app_commands.guild_only()
    async def proposal_cancel(self, interaction: discord.Interaction, number: int) -> None:
        if interaction.guild is None:
            return
        all_props = await self.bot.db.list_proposals(interaction.guild.id, limit=100)
        match = next((p for p in all_props if p.number == number), None)
        if match is None:
            await interaction.response.send_message(embed=error_embed("Не найдено"), ephemeral=True)
            return
        try:
            proposal = await self.bot.democracy_service.cancel(match.id, interaction.user.id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if proposal.message_id and interaction.guild:
            channel = interaction.guild.get_channel(proposal.channel_id or config.proposals_channel_id or 0)
            if channel and hasattr(channel, "fetch_message"):
                try:
                    msg = await channel.fetch_message(proposal.message_id)
                    embed = await build_proposal_embed(self.bot, proposal, config.timezone, final=True)
                    await msg.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass
        await interaction.response.send_message(embed=success_embed("Предложение отменено"))


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(DemocracyCog(bot))
