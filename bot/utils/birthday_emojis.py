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
SHORTCODE_RE = re.compile(r"^:?(?P<name>[A-Za-z0-9_]{2,32}):?$")
SHORTCODE_SEARCH_RE = re.compile(r":(?P<name>[A-Za-z0-9_]{2,32}):")
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

    shortcode = SHORTCODE_SEARCH_RE.search(value) or SHORTCODE_RE.match(value)
    if shortcode is not None:
        return f":{shortcode.group('name')}:"

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


_GUILD_EMOJI_CACHE: dict[int, list] = {}


def _emoji_candidates(guild: discord.Guild) -> list[discord.Emoji]:
    extra = _GUILD_EMOJI_CACHE.get(guild.id)
    if extra:
        return list(extra)
    return list(getattr(guild, "emojis", ()) or ())


def _find_guild_emoji(
    guild: discord.Guild,
    *,
    name: str | None = None,
    emoji_id: int | None = None,
) -> discord.Emoji | None:
    emojis = _emoji_candidates(guild)
    if emoji_id is not None:
        found = guild.get_emoji(emoji_id)
        if found is not None:
            return found
        for emoji in emojis:
            if emoji.id == emoji_id:
                return emoji
    if name:
        target = name.lower()
        for emoji in emojis:
            if emoji.name.lower() == target:
                return emoji
    return None


def guild_emoji_pool(guild: discord.Guild) -> list[discord.Emoji]:
    return _emoji_candidates(guild)


async def ensure_guild_emojis(guild: discord.Guild) -> None:
    try:
        fetched = await guild.fetch_emojis()
    except Exception:
        return
    if fetched:
        _GUILD_EMOJI_CACHE[guild.id] = list(fetched)


def expand_guild_shortcodes(guild: discord.Guild | None, text: str) -> str:
    if not text:
        return text
    cleaned = text.replace("\\_", "_")
    if guild is None:
        return cleaned

    def replace(match: re.Match[str]) -> str:
        emoji = _find_guild_emoji(guild, name=match.group("name"))
        return str(emoji) if emoji is not None else match.group(0)

    return SHORTCODE_SEARCH_RE.sub(replace, cleaned)


def format_text_with_guild_emojis(guild: discord.Guild | None, text: str) -> str:
    """Expand :name: then escape markdown without breaking <:name:id>."""
    if not text:
        return text
    expanded = expand_guild_shortcodes(guild, text)
    chunks: list[str] = []
    last = 0
    for match in CUSTOM_EMOJI_SEARCH_RE.finditer(expanded):
        chunks.append(escape_markdown_inline(expanded[last:match.start()]))
        chunks.append(match.group(0))
        last = match.end()
    chunks.append(escape_markdown_inline(expanded[last:]))
    return "".join(chunks)


def render_birthday_emoji(
    guild: discord.Guild | None,
    raw: str | None,
    *,
    user_id: int | None = None,
) -> str:
    """Turn stored `:name:` / name into `<:name:id>` for Discord messages."""
    if raw is None or not str(raw).strip():
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"
    if guild is None:
        return str(raw).strip()

    value = str(raw).strip()
    custom = CUSTOM_EMOJI_SEARCH_RE.search(value)
    if custom is not None:
        emoji = _find_guild_emoji(
            guild,
            name=custom.group("name"),
            emoji_id=int(custom.group("id")),
        )
        return str(emoji) if emoji is not None else custom.group(0)

    shortcode = SHORTCODE_SEARCH_RE.search(value) or SHORTCODE_RE.match(value)
    if shortcode is not None:
        emoji = _find_guild_emoji(guild, name=shortcode.group("name"))
        if emoji is not None:
            return str(emoji)
        return f":{shortcode.group('name')}:"

    if value.isidentifier():
        emoji = _find_guild_emoji(guild, name=value)
        if emoji is not None:
            return str(emoji)

    return value


def resolve_birthday_emoji(
    guild: discord.Guild | None,
    raw: str | None,
    *,
    user_id: int | None = None,
    strict: bool = True,
) -> str:
    if raw is None or not raw.strip():
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"

    value = raw.strip()
    rendered = render_birthday_emoji(guild, value, user_id=user_id)
    if CUSTOM_EMOJI_SEARCH_RE.search(rendered) or (
        rendered and not SHORTCODE_SEARCH_RE.search(rendered) and not SHORTCODE_RE.match(rendered)
    ):
        return rendered

    shortcode = SHORTCODE_SEARCH_RE.search(value) or SHORTCODE_RE.match(value)
    if strict and guild is not None and shortcode is not None:
        raise ValueError(f"Эмодзи :{shortcode.group('name')}: не найден на этом сервере")
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
