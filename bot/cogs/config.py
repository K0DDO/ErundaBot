"""Legacy config cog removed — use /.admin config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class ConfigCog(commands.Cog):
    """Kept so extension path stays stable; commands live in AdminCog."""

    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(ConfigCog(bot))
