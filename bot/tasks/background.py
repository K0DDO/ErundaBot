"""Centralized background task lifecycle."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from discord.ext import tasks

from bot.services.birthday_service import age_on, format_birthday_date
from bot.utils.embeds import base_embed

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class BackgroundTasks:
    """Starts/stops background loops once per bot lifecycle."""

    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            log.warning("BackgroundTasks.start() called more than once — ignored")
            return
        self._started = True
        if not self.birthday_loop.is_running():
            self.birthday_loop.start()
        log.info("Background tasks started")

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self.birthday_loop.is_running():
            self.birthday_loop.cancel()
        log.info("Background tasks stopped")

    @tasks.loop(minutes=1)
    async def birthday_loop(self) -> None:
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        try:
            guilds = await self.bot.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for birthday loop")
            return

        for config in guilds:
            guild = self.bot.get_guild(config.guild_id)
            if guild is None or config.birthday_channel_id is None:
                continue
            channel = guild.get_channel(config.birthday_channel_id)
            if channel is None or not hasattr(channel, "send"):
                continue

            try:
                local_today = now.astimezone(ZoneInfo(config.timezone)).date()
                announcements = await self.bot.birthday_service.due_announcements(config, now)
                for birthday in announcements:
                    age = age_on(birthday, local_today)
                    age_part = f" Исполняется {age}!" if age is not None else ""
                    embed = base_embed(
                        title="День рождения",
                        description=(
                            f"Сегодня день рождения у <@{birthday.user_id}>!\n"
                            f"Поздравляем!{age_part}"
                        ),
                    )
                    await channel.send(embed=embed)
                    await self.bot.birthday_service.mark_notified(
                        config.guild_id,
                        birthday.user_id,
                        local_today,
                        "announce",
                    )

                reminders = await self.bot.birthday_service.due_reminders(config, now)
                for birthday, event_date in reminders:
                    embed = base_embed(
                        title="Скоро день рождения",
                        description=(
                            f"Через {config.birthday_reminder_days} дн. день рождения у "
                            f"<@{birthday.user_id}> "
                            f"({format_birthday_date(birthday.day, birthday.month)})."
                        ),
                    )
                    await channel.send(embed=embed)
                    await self.bot.birthday_service.mark_notified(
                        config.guild_id,
                        birthday.user_id,
                        event_date,
                        "reminder",
                    )
            except Exception:
                log.exception("Birthday loop failed for guild %s", config.guild_id)

    @birthday_loop.before_loop
    async def before_birthday_loop(self) -> None:
        await self.bot.wait_until_ready()
