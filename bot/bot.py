"""Erunda bot application class."""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from bot.database.database import Database
from bot.services.ai_service import AIService
from bot.services.birthday_service import BirthdayService
from bot.services.birthday_star_service import BirthdayStarService
from bot.services.config_service import ConfigService
from bot.services.event_service import EventService
from bot.services.festival_service import FestivalService
from bot.services.festival_presence_service import FestivalPresenceService
from bot.services.quote_service import QuoteService
from bot.services.role_service import RoleService
from bot.services.statistics_service import StatisticsService
from bot.services.santa_service import SantaService
from bot.services.tarot_service import TarotService
from bot.services.tgk_service import TgkService
from bot.services.dossier_service import DossierService
from bot.services.voice_listen_service import VoiceListenService
from bot.tasks.background import BackgroundTasks

log = logging.getLogger(__name__)

COG_MODULES = (
    "bot.cogs.config",
    "bot.cogs.admin",
    "bot.cogs.birthdays",
    "bot.cogs.statistics",
    "bot.cogs.events",
    "bot.cogs.festival",
    "bot.cogs.tgk",
    "bot.cogs.tarot",
    "bot.cogs.santa",
    "bot.cogs.quotes",
    "bot.cogs.roles",
    "bot.cogs.voice",
)


class ErundaBot(commands.Bot):
    def __init__(
        self,
        *,
        database_url: str,
        default_timezone: str = "Europe/Moscow",
    ) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.guild_messages = True
        intents.message_content = True
        intents.guild_reactions = True
        intents.voice_states = True
        intents.emojis_and_stickers = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

        self.db = Database(database_url, default_timezone=default_timezone)
        self.config_service = ConfigService(self.db)
        self.birthday_service = BirthdayService(self.db)
        self.birthday_star_service = BirthdayStarService(self.db)
        self.ai_service = AIService()
        self.statistics_service = StatisticsService(self.db)
        self.event_service = EventService(self.db)
        self.festival_service = FestivalService(self.db, self.ai_service)
        self.festival_presence_service = FestivalPresenceService(
            self.db,
            self.festival_service,
        )
        self.festival_service.presence = self.festival_presence_service
        self.tgk_service = TgkService(self.db)
        self.tarot_service = TarotService(self.db)
        self.santa_service = SantaService(self.db)
        self.quote_service = QuoteService(self.db)
        self.role_service = RoleService(self.db)
        self.voice_listen_service = VoiceListenService(self.db, self.ai_service)
        self.dossier_service = DossierService(self.db, self.ai_service)
        self.voice_listen_service.dossier_service = self.dossier_service
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
        try:
            await self.voice_listen_service.stop_all()
        except Exception:
            log.exception("Failed to stop voice listen on shutdown")
        await self.db.close()
        await super().close()


def create_bot_from_env() -> ErundaBot:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    default_timezone = os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow")
    return ErundaBot(database_url=database_url, default_timezone=default_timezone)
