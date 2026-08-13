"""Color helpers for role customization."""

from __future__ import annotations

import colorsys


def parse_hex_color(value: str) -> int:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError("Цвет должен быть в формате #RRGGBB")
    return int(cleaned, 16)


def hsv_to_discord_color(hue: float, saturation: float = 0.92, value: float = 1.0) -> int:
    hue = hue % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    return (int(r * 255) << 16) | (int(g * 255) << 8) | int(b * 255)
