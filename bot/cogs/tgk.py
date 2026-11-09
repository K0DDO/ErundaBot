"""Telegram channel board sync (slash add/remove removed — use board buttons)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord.ext import commands

from bot.views.tgk_views import register_tgk_board_views

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class TgkCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._board_synced = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._board_synced:
            return
        self._board_synced = True
        for guild in self.bot.guilds:
            try:
                await self.bot.tgk_service.sync_board(guild, self.bot)
            except Exception:
                log.exception("Failed to sync TGK board for guild %s", guild.id)
        await register_tgk_board_views(self.bot)


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(TgkCog(bot))
