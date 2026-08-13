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
SHORTCODE_RE = re.compile(r"^:(?P<name>\w+):$")
GLUED_NAME_RE = re.compile(r"^(.+?)([A-Za-z\u0400-\u04FF][\w\u0400-\u04FF]*)$")
TRAILING_NAME_AFTER_SPACE_RE = re.compile(
    r"^(\S+)\s+([\w\u0400-\u04FF]+)$",
)


def clean_birthday_emoji(value: str) -> str:
    """Drop a name accidentally typed into the emoji field."""
    value = value.strip()
    if not value:
        return value
    if CUSTOM_EMOJI_RE.match(value) or value.startswith("<:") or value.startswith("<a:"):
        return value

    spaced = TRAILING_NAME_AFTER_SPACE_RE.match(value)
    if spaced is not None:
        return spaced.group(1)

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


def _find_guild_emoji(guild: discord.Guild, name: str) -> discord.Emoji | None:
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
    if CUSTOM_EMOJI_RE.match(value):
        return value

    shortcode = SHORTCODE_RE.match(value)
    if shortcode is not None:
        if guild is None:
            raise ValueError("Эмодзи сервера можно указать только на сервере")
        emoji = _find_guild_emoji(guild, shortcode.group("name"))
        if emoji is None:
            raise ValueError(f"Эмодзи {value} не найден на этом сервере")
        return str(emoji)

    if guild is not None and value.isidentifier():
        emoji = _find_guild_emoji(guild, value)
        if emoji is not None:
            return str(emoji)

    return clean_birthday_emoji(value)


def normalize_birthday_emoji(raw: str | None, *, user_id: int | None = None) -> str:
    if raw is None or not raw.strip():
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"
    return clean_birthday_emoji(raw.strip())
