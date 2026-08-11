"""Proposal voting UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from bot.utils.embeds import base_embed, error_embed, success_embed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.database.models import Proposal


async def build_proposal_embed(
    bot: ErundaBot,
    proposal: Proposal,
    tz_name: str,
    *,
    final: bool = False,
) -> discord.Embed:
    yes, no = await bot.democracy_service.vote_counts(proposal.id)
    total = yes + no
    ratio = (yes / total * 100) if total else 0.0
    time_left = bot.democracy_service.time_left_label(proposal, tz_name)

    if proposal.status == "open":
        title = f"🗳️ Предложение #{proposal.number}"
        footer = f"Голосование закончится через {time_left}."
    elif proposal.status == "passed":
        title = "✅ Предложение принято"
        footer = f"{yes} за\n{no} против\n\nРезультат: {ratio:.1f}%"
    elif proposal.status == "rejected":
        title = "❌ Предложение отклонено"
        footer = f"{yes} за\n{no} против"
    else:
        title = f"🗳️ Предложение #{proposal.number}"
        footer = "Отменено"

    embed = base_embed(
        title=title,
        description=(
            f"{proposal.content}\n\n"
            f"Автор: <@{proposal.author_id}>\n\n"
            f"👍 {yes}\n👎 {no}"
        ),
    )
    if not final and proposal.status == "open":
        embed.set_footer(text=footer)
    elif final or proposal.status in ("passed", "rejected"):
        embed.description += f"\n\n{footer}"
        embed.set_footer(text="Ерунда")
    return embed


class ProposalCreateModal(discord.ui.Modal, title="Новое предложение"):
    content = discord.ui.TextInput(
        label="Текст предложения",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
    )

    def __init__(self, bot: ErundaBot, guild_id: int, author_id: int, tz_name: str) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.author_id = author_id
        self.tz_name = tz_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            proposal = await self.bot.democracy_service.create(
                self.guild_id,
                self.author_id,
                str(self.content.value),
                self.tz_name,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return

        config = await self.bot.config_service.get(self.guild_id)
        channel_id = config.proposals_channel_id or interaction.channel_id
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message(
                embed=error_embed("Канал голосований не найден"),
                ephemeral=True,
            )
            return

        embed = await build_proposal_embed(self.bot, proposal, self.tz_name)
        view = ProposalView(self.bot, proposal.id)
        message = await channel.send(embed=embed, view=view)
        await self.bot.democracy_service.set_message(proposal.id, channel.id, message.id)
        self.bot.add_view(view, message_id=message.id)

        await interaction.response.send_message(
            embed=success_embed("Предложение создано", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )


class ProposalView(discord.ui.View):
    def __init__(self, bot: ErundaBot, proposal_id: int) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.proposal_id = proposal_id

        yes_btn = discord.ui.Button(
            label="👍 За",
            style=discord.ButtonStyle.success,
            custom_id=f"proposal:yes:{proposal_id}",
        )
        yes_btn.callback = self.vote_yes
        self.add_item(yes_btn)

        no_btn = discord.ui.Button(
            label="👎 Против",
            style=discord.ButtonStyle.danger,
            custom_id=f"proposal:no:{proposal_id}",
        )
        no_btn.callback = self.vote_no
        self.add_item(no_btn)

    async def _vote(self, interaction: discord.Interaction, yes: bool) -> None:
        try:
            proposal = await self.bot.democracy_service.vote(
                self.proposal_id,
                interaction.user.id,
                yes,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        config = await self.bot.config_service.get(proposal.guild_id)
        embed = await build_proposal_embed(self.bot, proposal, config.timezone)
        await interaction.response.edit_message(embed=embed, view=self)

    async def vote_yes(self, interaction: discord.Interaction) -> None:
        await self._vote(interaction, True)

    async def vote_no(self, interaction: discord.Interaction) -> None:
        await self._vote(interaction, False)


def register_proposal_views(bot: ErundaBot, proposals: list[Proposal]) -> None:
    for proposal in proposals:
        if proposal.message_id and proposal.status == "open":
            bot.add_view(ProposalView(bot, proposal.id), message_id=proposal.message_id)
