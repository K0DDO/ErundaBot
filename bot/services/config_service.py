"""Guild configuration service."""

from __future__ import annotations

from bot.database.database import Database
from bot.database.models import GuildConfig
from bot.utils.timezones import is_valid_timezone, parse_hhmm


class ConfigService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, guild_id: int) -> GuildConfig:
        return await self.db.ensure_guild(guild_id)

    async def set_channel(
        self,
        guild_id: int,
        field: str,
        channel_id: int | None,
    ) -> GuildConfig:
        allowed = {
            "birthday_channel_id",
            "events_channel_id",
            "quotes_channel_id",
            "fest_channel_id",
            "tgk_channel_id",
        }
        if field not in allowed:
            raise ValueError(f"Invalid channel field: {field}")
        return await self.db.update_guild(guild_id, **{field: channel_id})

    async def set_flag(self, guild_id: int, field: str, enabled: bool) -> GuildConfig:
        allowed = {
            "statistics_enabled",
            "personal_roles_enabled",
            "fest_presence_check",
        }
        if field not in allowed:
            raise ValueError(f"Invalid flag field: {field}")
        return await self.db.update_guild(guild_id, **{field: int(enabled)})

    async def set_role(self, guild_id: int, field: str, role_id: int | None) -> GuildConfig:
        allowed = {"fest_ping_role_id", "config_role_id", "tgk_list_role_id"}
        if field not in allowed:
            raise ValueError(f"Invalid role field: {field}")
        return await self.db.update_guild(guild_id, **{field: role_id})

    async def set_timezone(self, guild_id: int, timezone: str) -> GuildConfig:
        if not is_valid_timezone(timezone):
            raise ValueError(f"Unknown timezone: {timezone}")
        return await self.db.update_guild(guild_id, timezone=timezone)

    async def set_birthday_announce_time(self, guild_id: int, value: str) -> GuildConfig:
        if parse_hhmm(value) is None:
            raise ValueError("Time must be HH:MM (24h)")
        return await self.db.update_guild(guild_id, birthday_announce_time=value)

    async def set_int(self, guild_id: int, field: str, value: int) -> GuildConfig:
        bounds: dict[str, tuple[int, int]] = {
            "birthday_reminder_days": (0, 30),
            "fest_reminder_minutes": (0, 10080),
        }
        if field not in bounds:
            raise ValueError(f"Invalid int field: {field}")
        lo, hi = bounds[field]
        if not (lo <= value <= hi):
            raise ValueError(f"{field} must be between {lo} and {hi}")
        return await self.db.update_guild(guild_id, **{field: value})
