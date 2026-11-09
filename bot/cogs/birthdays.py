"""Birthday board sync (slash set/remove removed — use board buttons)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from discord.ext import commands

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class BirthdaysCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._board_synced = False
        self._old_greetings_cleaned = False

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
                try:
                    local_today = datetime.now(ZoneInfo(config.timezone)).date()
                    removed = await self.bot.birthday_service.cleanup_past_messages(
                        guild,
                        local_today,
                        history_limit=500,
                    )
                    if removed:
                        log.info(
                            "Removed %s old birthday greetings in guild %s",
                            removed,
                            guild.id,
                        )
                except Exception:
                    log.exception(
                        "Failed to cleanup old birthday greetings for guild %s",
                        guild.id,
                    )
        self._old_greetings_cleaned = True


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(BirthdaysCog(bot))
