"""Centralized background task lifecycle."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class BackgroundTasks:
    """Placeholder manager for future loops (birthdays, events, RGB, proposals).

    Tasks must be started once per bot lifecycle and cancelled on close.
    """

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
        log.info("Background tasks manager started (no loops yet)")

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        log.info("Background tasks manager stopped")
