"""Birthday display emoji helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

BIRTHDAY_EMOJI_POOL = (
    "🎂", "🎈", "🎁", "🎉", "🥳", "✨", "🌟", "💫", "🍰", "🧁",
    "🎊", "💖", "🌸", "🦄", "🐱", "🐶", "🦊", "🐸", "🍕", "🎮",
    "🌈", "☀️", "🌙", "🔥", "💎", "🎵", "🎨", "⚡", "🍀", "🚀",
)

CUSTOM_EMOJI_RE = re.compile(r"^<a?:(?P<name>\w+):(?P<id>\d+)>$")
CUSTOM_EMOJI_SEARCH_RE = re.compile(r"<a?:(?P<name>\w+):(?P<id>\d+)>")
SHORTCODE_RE = re.compile(r"^:?(?P<name>\w+):?$")
GLUED_NAME_RE = re.compile(r"^(.+?)([A-Za-z\u0400-\u04FF][\w\u0400-\u04FF]*)$")
TRAILING_NAME_AFTER_SPACE_RE = re.compile(
    r"^(\S+)\s+([\w\u0400-\u04FF]+)$",
)


def clean_birthday_emoji(value: str) -> str:
    """Drop a name accidentally typed into the emoji field."""
    value = value.strip()
    if not value:
        return value
    custom = CUSTOM_EMOJI_SEARCH_RE.search(value)
    if custom is not None:
        return custom.group(0)
    if value.startswith("<:") or value.startswith("<a:"):
        return value

    spaced = TRAILING_NAME_AFTER_SPACE_RE.match(value)
    if spaced is not None:
        return spaced.group(1)

    if value.isidentifier() or SHORTCODE_RE.match(value):
        return value.strip(":")

    glued = GLUED_NAME_RE.match(value)
    if glued is not None:
        emoji_part = glued.group(1).strip()
        if emoji_part:
            return emoji_part

    return value


def escape_markdown_inline(text: str) -> str:
    return re.sub(r"([\\*_~|`])", r"\\\1", text)


def pick_birthday_emoji(user_id: int) -> str:
    return BIRTHDAY_EMOJI_POOL[user_id % len(BIRTHDAY_EMOJI_POOL)]


def _find_guild_emoji(guild: discord.Guild, *, name: str | None = None, emoji_id: int | None = None) -> discord.Emoji | None:
    if emoji_id is not None:
        found = guild.get_emoji(emoji_id)
        if found is not None:
            return found
        for emoji in guild.emojis:
            if emoji.id == emoji_id:
                return emoji
    if name:
        target = name.lower()
        for emoji in guild.emojis:
            if emoji.name.lower() == target:
                return emoji
    return None


def resolve_birthday_emoji(
    guild: discord.Guild | None,
    raw: str | None,
    *,
    user_id: int | None = None,
) -> str:
    if raw is None or not raw.strip():
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"

    value = raw.strip()
    custom = CUSTOM_EMOJI_SEARCH_RE.search(value)
    if custom is not None:
        emoji_id = int(custom.group("id"))
        if guild is not None:
            emoji = _find_guild_emoji(guild, name=custom.group("name"), emoji_id=emoji_id)
            if emoji is not None:
                return str(emoji)
        return custom.group(0)

    shortcode = SHORTCODE_RE.match(value)
    name = shortcode.group("name") if shortcode is not None else None
    if name and guild is not None:
        emoji = _find_guild_emoji(guild, name=name)
        if emoji is not None:
            return str(emoji)
        if shortcode is not None and (value.startswith(":") or value.endswith(":")):
            raise ValueError(f"Эмодзи :{name}: не найден на этом сервере")

    if guild is not None and value.isidentifier():
        emoji = _find_guild_emoji(guild, name=value)
        if emoji is not None:
            return str(emoji)

    return clean_birthday_emoji(value)


def normalize_birthday_emoji(
    raw: str | None,
    *,
    user_id: int | None = None,
    guild: discord.Guild | None = None,
) -> str:
    if raw is None or not raw.strip():
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"
    if guild is not None:
        return resolve_birthday_emoji(guild, raw, user_id=user_id)
    custom = CUSTOM_EMOJI_SEARCH_RE.search(raw.strip())
    if custom is not None:
        return custom.group(0)
    return clean_birthday_emoji(raw.strip())
