"""Erunda bot application class."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands

from bot.database.database import Database
from bot.services.birthday_service import BirthdayService
from bot.services.config_service import ConfigService
from bot.services.statistics_service import StatisticsService
from bot.tasks.background import BackgroundTasks

log = logging.getLogger(__name__)

COG_MODULES = (
    "bot.cogs.config",
    "bot.cogs.birthdays",
    "bot.cogs.statistics",
)


class ErundaBot(commands.Bot):
    def __init__(
        self,
        *,
        database_path: str | Path,
        default_timezone: str = "Europe/Moscow",
    ) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.guild_messages = True
        intents.message_content = True
        intents.guild_reactions = True
        intents.voice_states = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        self.db = Database(database_path, default_timezone=default_timezone)
        self.config_service = ConfigService(self.db)
        self.birthday_service = BirthdayService(self.db)
        self.statistics_service = StatisticsService(self.db)
        self.background = BackgroundTasks(self)
        self._synced = False

    async def setup_hook(self) -> None:
        await self.db.connect()

        for module in COG_MODULES:
            await self.load_extension(module)
            log.info("Loaded extension %s", module)

        await self.background.start()

        if not self._synced:
            synced = await self.tree.sync()
            self._synced = True
            log.info("Synced %s application command(s)", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user and self.user.id)
        for guild in self.guilds:
            await self.db.ensure_guild(guild.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.db.ensure_guild(guild.id)
        log.info("Joined guild %s (%s)", guild.name, guild.id)

    async def close(self) -> None:
        await self.background.stop()
        await self.db.close()
        await super().close()


def create_bot_from_env() -> ErundaBot:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set")

    database_path = os.getenv("DATABASE_PATH", "./data/erunda.db")
    default_timezone = os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow")
    return ErundaBot(database_path=database_path, default_timezone=default_timezone)
