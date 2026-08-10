"""Formatting helpers."""

from __future__ import annotations


def bool_label(value: bool) -> str:
    return "вкл" if value else "выкл"


def channel_mention(channel_id: int | None) -> str:
    if channel_id is None:
        return "не задан"
    return f"<#{channel_id}>"


def format_duration(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def format_relative_span(delta_days: int) -> str:
    if delta_days < 0:
        delta_days = 0
    years, rem = divmod(delta_days, 365)
    months, days = divmod(rem, 30)
    parts: list[str] = []
    if years:
        parts.append(f"{years} г." if years > 1 else "1 г.")
        # prefer "год/года/лет" simple form:
        parts[-1] = _ru_years(years)
    if months:
        parts.append(_ru_months(months))
    if not parts:
        parts.append(_ru_days(days if days else delta_days))
    return " ".join(parts)


def _ru_years(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} год"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} года"
    return f"{n} лет"


def _ru_months(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} месяц"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} месяца"
    return f"{n} месяцев"


def _ru_days(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} дня"
    return f"{n} дней"
