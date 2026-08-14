"""Permission helpers."""

from __future__ import annotations

import discord


def is_guild_admin(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def can_edit_config(member: discord.Member, config_role_id: int | None = None) -> bool:
    if config_role_id is not None and any(role.id == config_role_id for role in member.roles):
        return True
    return is_guild_admin(member)


def config_denied_reason(config_role_id: int | None) -> str:
    if config_role_id is not None:
        return "Нужна роль доступа к /config."
    return "Нужны права администратора или Manage Server."


def bot_cannot_send_reason(guild: discord.Guild, channel: discord.abc.Snowflake) -> str | None:
    bot_member = guild.me
    if bot_member is None:
        return "Бот не найден на сервере"
    resolved = guild.get_channel(channel.id)
    if resolved is None:
        return "Канал не найден"
    perms = resolved.permissions_for(bot_member)
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("Просмотр канала")
    if not perms.send_messages:
        missing.append("Отправка сообщений")
    if not perms.embed_links:
        missing.append("Встраивание ссылок")
    if not missing:
        return None
    return (
        f"У бота нет прав в {resolved.mention}: {', '.join(missing)}. "
        "Выдай их роли бота в настройках канала."
    )


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
