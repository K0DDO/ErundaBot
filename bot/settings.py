"""Runtime settings from environment."""

from __future__ import annotations

import os

# Discord snowflake of @k0ddo — required for owner-only ACL.
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0") or "0")
# Ерундульки: soundboard + festival numbering continues from 29.
HOME_GUILD_ID = int(
    os.getenv("HOME_GUILD_ID", "1497627892589531202") or "1497627892589531202"
)
# Owner DM when VPS root used space reaches this many GiB (30G disk → ~5G free).
DISK_ALERT_USED_GIB = float(os.getenv("DISK_ALERT_USED_GIB", "25") or "25")
DISK_ALERT_PATH = os.getenv("DISK_ALERT_PATH", "/") or "/"
# Voice→text / voice_listen: Ерундульки + Пепелендовский экологический центр.
_DEFAULT_VOICE_LISTEN_GUILDS = f"{HOME_GUILD_ID},1546175604032802907"


def _parse_snowflake_set(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError:
            continue
    return frozenset(values)


VOICE_LISTEN_GUILD_IDS = _parse_snowflake_set(
    os.getenv("VOICE_LISTEN_GUILD_IDS", _DEFAULT_VOICE_LISTEN_GUILDS)
    or _DEFAULT_VOICE_LISTEN_GUILDS
)
if not VOICE_LISTEN_GUILD_IDS:
    VOICE_LISTEN_GUILD_IDS = frozenset({HOME_GUILD_ID})


def is_home_guild(guild_id: int) -> bool:
    return int(guild_id) == HOME_GUILD_ID


def voice_listen_allowed(guild_id: int) -> bool:
    return int(guild_id) in VOICE_LISTEN_GUILD_IDS
