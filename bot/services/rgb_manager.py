"""Centralized RGB role animation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from bot.utils.colors import hsv_to_discord_color
from bot.utils.permissions import bot_can_manage_role

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.bot import ErundaBot


class RgbManager:
    """Single loop updating all RGB-enabled roles."""

    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("RGB manager started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("RGB manager stopped")

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("RGB tick failed")
            await asyncio.sleep(10)

    async def _tick(self) -> None:
        roles = await self.bot.db.list_rgb_roles()
        for record in roles:
            if not self._running:
                break
            config = await self.bot.db.get_guild(record.guild_id)
            if config is None or not config.rgb_enabled:
                continue
            guild = self.bot.get_guild(record.guild_id)
            if guild is None:
                continue
            bot_member = guild.me
            if bot_member is None:
                continue
            role = guild.get_role(record.role_id)
            if role is None:
                await self.bot.db.delete_custom_role_record(record.guild_id, record.role_id)
                continue
            if not bot_can_manage_role(bot_member, role):
                continue
            interval = max(10, config.rgb_interval_seconds)
            step = 360.0 / max(36, 36 / record.rgb_speed)
            new_hue = (record.rgb_hue + step) % 360.0
            color = hsv_to_discord_color(new_hue)
            try:
                await role.edit(colour=discord.Colour(color))
                await self.bot.db.update_custom_role(
                    record.guild_id,
                    record.role_id,
                    rgb_hue=new_hue,
                )
            except discord.HTTPException:
                log.warning("RGB edit failed for role %s", record.role_id)
            await asyncio.sleep(interval / max(len(roles), 1))
