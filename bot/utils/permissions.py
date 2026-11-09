"""Permission helpers."""

from __future__ import annotations

import discord

from bot.settings import OWNER_USER_ID


def is_owner(user: discord.abc.User | discord.Member | None) -> bool:
    if user is None or OWNER_USER_ID <= 0:
        return False
    return int(user.id) == OWNER_USER_ID


def is_guild_admin(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


def can_staff(member: discord.Member) -> bool:
    """Owner or guild admin — replaces former Кино / config_role checks."""
    return is_owner(member) or is_guild_admin(member)


def staff_denied_reason() -> str:
    return "Недостаточно прав"


def can_edit_config(member: discord.Member, config_role_id: int | None = None) -> bool:
    # config_role_id ignored — kept for call-site compatibility during refactor.
    _ = config_role_id
    return can_staff(member)


def config_denied_reason(config_role_id: int | None = None) -> str:
    _ = config_role_id
    return staff_denied_reason()


def owner_denied_reason() -> str:
    return "Недостаточно прав"


def find_member_by_nickname(guild: discord.Guild, nickname: str) -> discord.Member:
    query = nickname.strip()
    if not query:
        raise ValueError("Укажи ник участника")
    lowered = query.casefold()
    matches: list[discord.Member] = []
    for member in guild.members:
        names = {member.display_name.casefold(), member.name.casefold()}
        if member.global_name:
            names.add(member.global_name.casefold())
        if lowered in names:
            matches.append(member)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("Несколько участников с таким ником — уточни")
    raise ValueError(f"Участник «{query}» не найден на сервере")


async def find_member_by_nickname_async(guild: discord.Guild, nickname: str) -> discord.Member:
    try:
        return find_member_by_nickname(guild, nickname)
    except ValueError as exc:
        if "не найден" not in str(exc):
            raise
    query = nickname.strip()
    queried = await guild.query_members(query=query, limit=25)
    lowered = query.casefold()
    matches: list[discord.Member] = []
    for member in queried:
        names = {member.display_name.casefold(), member.name.casefold()}
        if member.global_name:
            names.add(member.global_name.casefold())
        if lowered in names:
            matches.append(member)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("Несколько участников с таким ником — уточни")
    raise ValueError(f"Участник «{query}» не найден на сервере")


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
