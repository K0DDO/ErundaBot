"""Role management business logic."""

from __future__ import annotations

import logging

import discord

from bot.database.database import Database
from bot.database.models import CustomRole
from bot.utils.colors import parse_hex_color
from bot.utils.permissions import assert_bot_can_manage_role, fetch_bot_member, is_guild_admin

log = logging.getLogger(__name__)


class RoleService:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    async def position_personal_role(bot_member: discord.Member, role: discord.Role) -> None:
        """Move personal role high enough so its colour is visible in the member list."""
        if not bot_member.guild_permissions.manage_roles:
            return
        if role.is_default() or role.managed:
            return
        if role >= bot_member.top_role:
            return
        target = bot_member.top_role.position - 1
        if target < 1 or role.position >= target:
            return
        try:
            await role.edit(
                position=target,
                reason="Ерунда: персональная роль выше цветных ролей",
            )
        except discord.HTTPException:
            log.warning("Could not reposition personal role %s", role.id)

    async def create_managed_role(
        self,
        guild: discord.Guild,
        bot_member: discord.Member,
        name: str,
        color: int | None = None,
    ) -> tuple[discord.Role, CustomRole]:
        if not bot_member.guild_permissions.manage_roles:
            raise ValueError("У бота нет права Manage Roles")
        role = await guild.create_role(name=name[:100], colour=discord.Colour(color or 0))
        record = await self.db.save_custom_role(
            guild.id,
            role.id,
            owner_id=None,
            kind="managed",
        )
        return role, record

    async def edit_role(
        self,
        guild: discord.Guild,
        bot_member: discord.Member,
        role: discord.Role,
        *,
        name: str | None = None,
        color: int | None = None,
    ) -> CustomRole:
        assert_bot_can_manage_role(bot_member, role)
        kwargs: dict = {}
        if name is not None:
            kwargs["name"] = name[:100]
        if color is not None:
            kwargs["colour"] = discord.Colour(color)
        await role.edit(**kwargs)
        record = await self.db.get_custom_role_by_role_id(guild.id, role.id)
        if record is None:
            record = await self.db.save_custom_role(guild.id, role.id, None, "managed")
        return record

    async def delete_role(
        self,
        guild: discord.Guild,
        bot_member: discord.Member,
        role: discord.Role,
    ) -> None:
        assert_bot_can_manage_role(bot_member, role)
        await role.delete(reason="Ерунда: удаление роли")
        await self.db.delete_custom_role_record(guild.id, role.id)

    async def get_or_create_personal_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        bot_member: discord.Member,
    ) -> tuple[discord.Role, CustomRole]:
        config = await self.db.ensure_guild(guild.id)
        if not config.personal_roles_enabled:
            raise ValueError("Персональные роли отключены в /config")
        existing = await self.db.get_personal_role(guild.id, member.id)
        if existing:
            role = guild.get_role(existing.role_id)
            if role is None:
                await self.db.delete_custom_role_record(guild.id, existing.role_id)
            else:
                await self.position_personal_role(bot_member, role)
                return role, existing
        if not bot_member.guild_permissions.manage_roles:
            raise ValueError("У бота нет права Manage Roles")
        role = await guild.create_role(
            name=member.display_name[:100],
            reason="Ерунда: персональная роль",
        )
        try:
            await member.add_roles(role, reason="Ерунда: персональная роль")
        except discord.HTTPException as exc:
            await role.delete()
            raise ValueError("Не удалось выдать роль") from exc
        await self.position_personal_role(bot_member, role)
        record = await self.db.save_custom_role(
            guild.id,
            role.id,
            owner_id=member.id,
            kind="personal",
        )
        return role, record

    async def update_personal_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        bot_member: discord.Member | None = None,
        *,
        name: str | None = None,
        color: int | None = None,
    ) -> tuple[discord.Role, CustomRole]:
        bot_member = bot_member or await fetch_bot_member(guild)
        record = await self.db.get_personal_role(guild.id, member.id)
        if record is None:
            role, record = await self.get_or_create_personal_role(guild, member, bot_member)
        else:
            role = guild.get_role(record.role_id)
            if role is None:
                raise ValueError("Роль не найдена, создайте заново через /myrole")
        if record.owner_id != member.id:
            raise ValueError("Это не ваша роль")
        await self.position_personal_role(bot_member, role)
        role = guild.get_role(role.id)
        if role is None:
            raise ValueError("Роль не найдена, создайте заново через /myrole")
        assert_bot_can_manage_role(bot_member, role)
        edit_kwargs: dict = {}
        if name is not None:
            edit_kwargs["name"] = name[:100]
        if color is not None:
            edit_kwargs["colour"] = discord.Colour(color)
        if edit_kwargs:
            await role.edit(**edit_kwargs)
        await self.position_personal_role(bot_member, role)
        return role, record

    async def delete_personal_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        bot_member: discord.Member | None = None,
    ) -> None:
        bot_member = bot_member or await fetch_bot_member(guild)
        record = await self.db.get_personal_role(guild.id, member.id)
        if record is None:
            raise ValueError("У вас нет персональной роли")
        if record.owner_id != member.id:
            raise ValueError("Можно удалить только свою роль")
        role = guild.get_role(record.role_id)
        if role is not None:
            assert_bot_can_manage_role(bot_member, role)
            await role.delete(reason="Ерунда: удаление персональной роли")
        await self.db.delete_custom_role_record(guild.id, record.role_id)

    @staticmethod
    def parse_color(value: str | None) -> int | None:
        if not value or not value.strip():
            return None
        return parse_hex_color(value)

    @staticmethod
    def assert_admin(member: discord.Member) -> None:
        if not is_guild_admin(member):
            raise ValueError("Нужны права администратора или Manage Server")
