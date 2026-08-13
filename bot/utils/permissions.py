"""Permission helpers."""

from __future__ import annotations

import discord


def is_guild_admin(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def bot_can_manage_role(bot_member: discord.Member, role: discord.Role) -> bool:
    if role.is_default() or role.managed:
        return False
    top = bot_member.top_role
    return top > role and bot_member.guild_permissions.manage_roles


def role_manage_error(bot_member: discord.Member, role: discord.Role) -> str:
    if role.is_default():
        return "Нельзя изменить роль @everyone"
    if role.managed:
        return (
            f"Роль «{role.name}» управляется интеграцией (бот, буст, приложение), "
            "бот не может её менять"
        )
    if not bot_member.guild_permissions.manage_roles:
        return (
            "У бота нет права **Manage Roles** (Управление ролями). "
            "Включите его в настройках роли бота"
        )
    top = bot_member.top_role
    if top <= role:
        return (
            f"Роль бота «{top.name}» (позиция {top.position}) должна быть **выше** "
            f"персональной «{role.name}» (позиция {role.position}) в списке ролей сервера. "
            "Перетащите роль бота выше персональных ролей участников"
        )
    return "Бот не может изменить эту роль"


def assert_bot_can_manage_role(bot_member: discord.Member, role: discord.Role) -> None:
    if not bot_can_manage_role(bot_member, role):
        raise ValueError(role_manage_error(bot_member, role))


async def fetch_bot_member(guild: discord.Guild) -> discord.Member:
    if guild.me is None:
        raise ValueError("Бот не найден на сервере")
    try:
        return await guild.fetch_member(guild.me.id)
    except discord.HTTPException:
        return guild.me
