"""Color helpers for role customization."""

from __future__ import annotations


def parse_hex_color(value: str) -> int:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError("Цвет должен быть в формате #RRGGBB")
    return int(cleaned, 16)
