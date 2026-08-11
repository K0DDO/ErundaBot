"""Color helpers for RGB roles."""

from __future__ import annotations

import colorsys


def hsv_to_discord_color(hue: float, saturation: float = 1.0, value: float = 1.0) -> int:
    """Convert hue (0-360) to Discord color integer."""
    h = (hue % 360.0) / 360.0
    r, g, b = colorsys.hsv_to_rgb(h, saturation, value)
    return (int(r * 255) << 16) + (int(g * 255) << 8) + int(b * 255)


def parse_hex_color(value: str) -> int:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError("Цвет должен быть в формате #RRGGBB")
    return int(cleaned, 16)
