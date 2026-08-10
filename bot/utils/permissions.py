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
