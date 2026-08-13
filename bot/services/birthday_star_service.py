"""Birthday star RGB role: one shared role, max two people at a time."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord

from bot.database.database import Database
from bot.database.models import Birthday, GuildConfig
from bot.services.birthday_service import occurrence_on_year
from bot.utils.colors import hsv_to_discord_color
from bot.utils.permissions import assert_bot_can_manage_role, fetch_bot_member

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)

STAR_ROLE_NAME = "🎂 Именинник"
MAX_STAR_HOLDERS = 2
RGB_HUE_STEP = 0.08


class BirthdayStarService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._hue = 0.0
        self._rgb_backoff_until: datetime | None = None

    async def get_or_create_star_role(
        self,
        guild: discord.Guild,
        bot_member: discord.Member | None = None,
    ) -> discord.Role:
        config = await self.db.ensure_guild(guild.id)
        bot_member = bot_member or await fetch_bot_member(guild)
        role = guild.get_role(config.birthday_star_role_id) if config.birthday_star_role_id else None
        if role is None:
            if not bot_member.guild_permissions.manage_roles:
                raise ValueError("У бота нет права Manage Roles")
            role = await guild.create_role(
                name=STAR_ROLE_NAME,
                colour=discord.Colour(hsv_to_discord_color(0.0)),
                hoist=True,
                mentionable=False,
                reason="Ерунда: роль именинника",
            )
            await self.db.set_birthday_star_role_id(guild.id, role.id)
            await self.db.save_custom_role(
                guild.id,
                role.id,
                owner_id=None,
                kind="managed",
                rgb_enabled=True,
            )
        assert_bot_can_manage_role(bot_member, role)
        await self._position_star_role(bot_member, role)
        return role

    @staticmethod
    async def _position_star_role(bot_member: discord.Member, role: discord.Role) -> None:
        if role >= bot_member.top_role:
            return
        target = bot_member.top_role.position - 1
        if target < 1 or role.position >= target:
            return
        try:
            await role.edit(position=target, reason="Ерунда: роль именинника выше цветных ролей")
        except discord.HTTPException:
            log.warning("Could not reposition birthday star role %s", role.id)

    async def _personal_roles_to_hide(
        self,
        guild: discord.Guild,
        member: discord.Member,
        star_role: discord.Role,
    ) -> list[discord.Role]:
        record = await self.db.get_personal_role(guild.id, member.id)
        if record is None:
            return []
        role = guild.get_role(record.role_id)
        if (
            role is None
            or role.id == star_role.id
            or role not in member.roles
            or role.is_default()
            or role.managed
        ):
            return []
        return [role]

    async def grant(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        granted_on: date | None = None,
        force: bool = False,
    ) -> str:
        bot_member = await fetch_bot_member(guild)
        star_role = await self.get_or_create_star_role(guild, bot_member)
        existing = await self.db.get_birthday_star_grant(guild.id, member.id)
        if existing is not None and star_role in member.roles and not force:
            return f"{member.mention} уже именинник"
        holders = await self.db.list_birthday_star_grants(guild.id)
        if existing is None and len(holders) >= MAX_STAR_HOLDERS and not force:
            raise ValueError(
                f"Уже выдано {MAX_STAR_HOLDERS} ролей именинника. "
                "Снимите одну через `/birthday test-rgb-off`"
            )
        hidden_roles = await self._personal_roles_to_hide(guild, member, star_role)
        if hidden_roles:
            try:
                await member.remove_roles(*hidden_roles, reason="Ерунда: RGB именинника поверх цвета")
            except discord.HTTPException as extra:
                raise ValueError("Не удалось временно снять персональную роль") from extra
        try:
            await member.add_roles(star_role, reason="Ерунда: день рождения")
        except discord.HTTPException as exc:
            if hidden_roles:
                try:
                    await member.add_roles(*hidden_roles, reason="Ерунда: откат после ошибки именинника")
                except discord.HTTPException:
                    pass
            raise ValueError("Не удалось выдать роль именинника") from exc
        today = granted_on or datetime.now(ZoneInfo("Europe/Moscow")).date()
        await self.db.save_birthday_star_grant(
            guild.id,
            member.id,
            json.dumps([role.id for role in hidden_roles]),
            today.isoformat(),
        )
        return f"{member.mention} получил роль **{star_role.name}**"

    async def revoke(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str:
        grant = await self.db.get_birthday_star_grant(guild.id, member.id)
        config = await self.db.ensure_guild(guild.id)
        star_role = guild.get_role(config.birthday_star_role_id) if config.birthday_star_role_id else None
        if grant is not None:
            try:
                hidden_ids = [int(value) for value in json.loads(grant.hidden_role_ids or "[]")]
            except (TypeError, ValueError, json.JSONDecodeError):
                hidden_ids = []
            restore_roles = [role for role_id in hidden_ids if (role := guild.get_role(role_id)) is not None]
            if restore_roles:
                try:
                    await member.add_roles(*restore_roles, reason="Ерунда: вернуть цвет после ДР")
                except discord.HTTPException:
                    log.warning("Could not restore roles for %s in guild %s", member.id, guild.id)
            await self.db.delete_birthday_star_grant(guild.id, member.id)
        if star_role is not None and star_role in member.roles:
            try:
                await member.remove_roles(star_role, reason="Ерунда: день рождения закончился")
            except discord.HTTPException:
                log.warning("Could not remove star role from %s", member.id)
        return f"Роль именинника снята с {member.mention}"

    async def sync_today(
        self,
        guild: discord.Guild,
        config: GuildConfig,
        today: date,
        bot: ErundaBot | None = None,
    ) -> None:
        del bot
        birthdays = await self.db.list_birthdays(guild.id)
        today_ids: list[int] = []
        for birthday in birthdays:
            if occurrence_on_year(birthday.day, birthday.month, today.year) == today:
                today_ids.append(birthday.user_id)

        grants = await self.db.list_birthday_star_grants(guild.id)
        for grant in grants:
            if grant.user_id in today_ids:
                continue
            member = guild.get_member(grant.user_id)
            if member is None:
                await self.db.delete_birthday_star_grant(guild.id, grant.user_id)
                continue
            try:
                await self.revoke(guild, member)
            except Exception:
                log.exception("Failed to revoke birthday star from %s", grant.user_id)

        holders = {grant.user_id for grant in await self.db.list_birthday_star_grants(guild.id)}
        for user_id in today_ids:
            if user_id in holders:
                continue
            if len(holders) >= MAX_STAR_HOLDERS:
                break
            member = guild.get_member(user_id)
            if member is None:
                continue
            try:
                await self.grant(guild, member, granted_on=today)
                holders.add(user_id)
            except Exception:
                log.exception("Failed to grant birthday star to %s", user_id)

    async def tick_rgb(self, bot: ErundaBot) -> None:
        now = datetime.now()
        if self._rgb_backoff_until is not None and now < self._rgb_backoff_until:
            return
        try:
            guilds = await self.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for birthday RGB")
            return

        self._hue = (self._hue + RGB_HUE_STEP) % 1.0
        color = discord.Colour(hsv_to_discord_color(self._hue))
        updated_any = False
        for config in guilds:
            if not config.birthday_star_role_id:
                continue
            guild = bot.get_guild(config.guild_id)
            if guild is None:
                continue
            role = guild.get_role(config.birthday_star_role_id)
            if role is None:
                continue
            holders = await self.db.list_birthday_star_grants(guild.id)
            if not holders:
                continue
            try:
                bot_member = guild.me or await fetch_bot_member(guild)
                if not bot_member.guild_permissions.manage_roles or role >= bot_member.top_role:
                    continue
                await role.edit(colour=color, reason="Ерунда: RGB именинника")
                updated_any = True
            except discord.HTTPException as exc:
                retry_after = getattr(exc, "retry_after", None)
                seconds = float(retry_after) if retry_after else 15.0
                from datetime import timedelta

                self._rgb_backoff_until = now + timedelta(seconds=max(seconds, 8.0))
                log.warning("Birthday RGB rate limited, backoff %.1fs", seconds)
                return
            except Exception:
                log.exception("Birthday RGB tick failed for guild %s", guild.id)
        if not updated_any:
            self._hue = (self._hue - RGB_HUE_STEP) % 1.0
