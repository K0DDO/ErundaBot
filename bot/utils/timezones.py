"""Timezone helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except ZoneInfoNotFoundError:
        return False


def now_in_timezone(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def parse_hhmm(value: str) -> tuple[int, int] | None:
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_event_datetime(date_str: str, time_str: str, tz_name: str) -> datetime:
    """Parse DD.MM.YYYY or DD.MM + HH:MM in guild timezone."""
    date_str = date_str.strip()
    time_str = time_str.strip()
    parts = date_str.split(".")
    if len(parts) not in (2, 3):
        raise ValueError("Дата: DD.MM или DD.MM.YYYY")
    try:
        day = int(parts[0])
        month = int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else datetime.now(ZoneInfo(tz_name)).year
    except ValueError as exc:
        raise ValueError("Некорректная дата") from exc

    parsed_time = parse_hhmm(time_str)
    if parsed_time is None:
        raise ValueError("Время: HH:MM (24ч)")
    hour, minute = parsed_time

    try:
        return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz_name))
    except ValueError as exc:
        raise ValueError("Некорректная дата или время") from exc


def format_countdown(delta_seconds: float) -> str:
    if delta_seconds <= 0:
        return "сеанс уже начался"
    total = int(delta_seconds)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{max(minutes, 1)} мин"


def format_datetime_local(dt: datetime, tz_name: str) -> tuple[str, str]:
    """Return (date_label, time_label) in guild timezone."""
    local = dt.astimezone(ZoneInfo(tz_name))
    months = (
        "", "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    date_label = f"{local.day} {months[local.month]}"
    time_label = local.strftime("%H:%M")
    return date_label, time_label
