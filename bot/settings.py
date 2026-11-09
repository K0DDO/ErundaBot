"""Runtime settings from environment."""

from __future__ import annotations

import os

# Discord snowflake of @k0ddo — required for owner-only ACL.
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0") or "0")
# Ерундульки: soundboard + festival numbering continues from 29.
HOME_GUILD_ID = int(
    os.getenv("HOME_GUILD_ID", "1497627892589531202") or "1497627892589531202"
)
OWNER_MENTION = f"<@{OWNER_USER_ID}>" if OWNER_USER_ID else "@k0ddo"
OWNER_ONLY_HINT = f"доступ только у {OWNER_MENTION}"


def is_home_guild(guild_id: int) -> bool:
    return int(guild_id) == HOME_GUILD_ID
