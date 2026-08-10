"""Shared embed styling for Ерунда."""

from __future__ import annotations

import discord

BRAND_COLOR = 0x7C9CFF
SUCCESS_COLOR = 0x57F287
WARNING_COLOR = 0xFEE75C
ERROR_COLOR = 0xED4245


def base_embed(
    *,
    title: str | None = None,
    description: str | None = None,
    color: int = BRAND_COLOR,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Ерунда")
    return embed


def success_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(title=title, description=description, color=SUCCESS_COLOR)


def error_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(title=title, description=description, color=ERROR_COLOR)


def warning_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(title=title, description=description, color=WARNING_COLOR)
