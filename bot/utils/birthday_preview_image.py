"""Render birthday preview as a Discord-like chat image."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from bot.services.birthday_service import format_birthday_date, member_display

if TYPE_CHECKING:
    import discord

    from bot.services.birthday_service import BirthdayEntry

log = logging.getLogger(__name__)

SCALE = 2

PREVIEW_WIDTH = 520 * SCALE
PADDING = 16 * SCALE
AVATAR_SIZE = 40 * SCALE
ROW_HEIGHT = 56 * SCALE
ROW_GAP = 4 * SCALE
TEXT_GAP = 12 * SCALE
NAME_Y_OFFSET = 6 * SCALE
DATE_Y_OFFSET = 28 * SCALE
MORE_Y_OFFSET = 18 * SCALE
NAME_FONT_SIZE = 16 * SCALE
DATE_FONT_SIZE = 14 * SCALE
AVATAR_FETCH_SIZE = 256

BG_COLOR = (49, 51, 56)  # #313338
DEFAULT_NAME_COLOR = (242, 243, 245)  # #f2f3f5
DATE_COLOR = (148, 155, 164)  # #949ba4
MORE_COLOR = (114, 118, 125)  # #72767d

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if bold:
        bold_candidates = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
        )
        for path in bold_candidates:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _circle_avatar(image: Image.Image, size: int) -> Image.Image:
    image = image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(image, (0, 0), mask)
    return output


def _member_name_color(guild: discord.Guild, user_id: int) -> tuple[int, int, int]:
    member = guild.get_member(user_id)
    if member is None:
        return DEFAULT_NAME_COLOR
    colour = member.colour
    if colour.value == 0:
        return DEFAULT_NAME_COLOR
    return colour.to_rgb()


def _avatar_url(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    if member is not None:
        return member.display_avatar.replace(size=AVATAR_FETCH_SIZE).url
    return f"https://cdn.discordapp.com/embed/avatars/{user_id % 5}.png"


def _entry_timing(entry: BirthdayEntry) -> str:
    if entry.days_until == 0:
        return "сегодня"
    if entry.days_until == 1:
        return "завтра"
    return f"через {entry.days_until} дн."


async def _fetch_avatar(
    session: aiohttp.ClientSession,
    url: str,
) -> Image.Image | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status != 200:
                return None
            data = await response.read()
        return Image.open(io.BytesIO(data))
    except Exception:
        log.debug("Failed to fetch avatar %s", url, exc_info=True)
        return None


async def render_birthday_preview_image(
    guild: discord.Guild,
    entries: list[BirthdayEntry],
    *,
    limit: int = 25,
) -> bytes:
    shown = entries[:limit]
    extra = len(entries) - len(shown)

    row_count = len(shown) + (1 if extra > 0 else 0)
    height = PADDING * 2 + row_count * ROW_HEIGHT + max(0, row_count - 1) * ROW_GAP

    canvas = Image.new("RGB", (PREVIEW_WIDTH, height), BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    name_font = _load_font(NAME_FONT_SIZE, bold=True)
    date_font = _load_font(DATE_FONT_SIZE)

    async with aiohttp.ClientSession() as session:
        for index, entry in enumerate(shown):
            y = PADDING + index * (ROW_HEIGHT + ROW_GAP)
            avatar_x = PADDING
            text_x = PADDING + AVATAR_SIZE + TEXT_GAP
            user_id = entry.birthday.user_id

            avatar = await _fetch_avatar(session, _avatar_url(guild, user_id))
            if avatar is not None:
                circled = _circle_avatar(avatar, AVATAR_SIZE)
                canvas.paste(
                    circled,
                    (avatar_x, y + (ROW_HEIGHT - AVATAR_SIZE) // 2),
                    circled,
                )

            name = member_display(guild, user_id)
            when = format_birthday_date(entry.birthday.day, entry.birthday.month)
            date_line = f"{when} ({_entry_timing(entry)})"

            draw.text(
                (text_x, y + NAME_Y_OFFSET),
                name,
                fill=_member_name_color(guild, user_id),
                font=name_font,
            )
            draw.text(
                (text_x, y + DATE_Y_OFFSET),
                date_line,
                fill=DATE_COLOR,
                font=date_font,
            )

        if extra > 0:
            y = PADDING + len(shown) * (ROW_HEIGHT + ROW_GAP)
            draw.text(
                (PADDING, y + MORE_Y_OFFSET),
                f"…и ещё {extra}",
                fill=MORE_COLOR,
                font=date_font,
            )

    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()
