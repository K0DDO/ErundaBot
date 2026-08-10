"""Formatting helpers."""

from __future__ import annotations


def bool_label(value: bool) -> str:
    return "вкл" if value else "выкл"


def channel_mention(channel_id: int | None) -> str:
    if channel_id is None:
        return "не задан"
    return f"<#{channel_id}>"
