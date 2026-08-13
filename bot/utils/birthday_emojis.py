"""Birthday display emoji helpers."""

BIRTHDAY_EMOJI_POOL = (
    "🎂", "🎈", "🎁", "🎉", "🥳", "✨", "🌟", "💫", "🍰", "🧁",
    "🎊", "💖", "🌸", "🦄", "🐱", "🐶", "🦊", "🐸", "🍕", "🎮",
    "🌈", "☀️", "🌙", "🔥", "💎", "🎵", "🎨", "⚡", "🍀", "🚀",
)


def pick_birthday_emoji(user_id: int) -> str:
    return BIRTHDAY_EMOJI_POOL[user_id % len(BIRTHDAY_EMOJI_POOL)]


def normalize_birthday_emoji(raw: str | None, *, user_id: int | None = None) -> str:
    if raw is None:
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"
    value = raw.strip()
    if not value:
        return pick_birthday_emoji(user_id) if user_id is not None else "🎂"
    return value
