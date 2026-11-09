"""Secret Santa public commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.santa_service import can_santa_draw
from bot.utils.embeds import error_embed, success_embed
from bot.utils.permissions import staff_denied_reason
from bot.views.santa_views import refresh_santa_message, register_santa_views

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class SantaCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self._register_views())

    async def _register_views(self) -> None:
        await self.bot.wait_until_ready()
        await register_santa_views(self.bot)

    santa = app_commands.Group(name="santa", description="Тайный Санта")

    @santa.command(
        name="start",
        description="Провести розыгрыш и разослать ЛС",
    )
    @app_commands.guild_only()
    async def santa_start(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_santa_draw(interaction.user, config):
            await interaction.response.send_message(
                embed=error_embed(staff_denied_reason()),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            event = await self.bot.santa_service.require_active(interaction.guild.id)
            ok, failed = await self.bot.santa_service.draw_and_notify(
                self.bot,
                interaction.guild,
                event,
            )
            event = await self.bot.db.get_santa_event(event.id)
            if event is not None:
                await refresh_santa_message(self.bot, event)
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        except Exception:
            log.exception("Santa draw failed")
            await interaction.followup.send(
                embed=error_embed("Не получилось провести розыгрыш"),
                ephemeral=True,
            )
            return

        parts: list[str] = []
        if ok:
            parts.append("**Отправлено:**\n" + "\n".join(f"• {line}" for line in ok))
        if failed:
            parts.append(
                "**Не удалось отправить:**\n" + "\n".join(f"• {line}" for line in failed)
            )
        await interaction.followup.send(
            embed=success_embed(
                "Розыгрыш готов",
                "\n\n".join(parts) if parts else "Пар нет",
            ),
            ephemeral=True,
        )


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(SantaCog(bot))
