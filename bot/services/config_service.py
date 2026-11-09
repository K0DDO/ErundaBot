"""Guild configuration service."""

from __future__ import annotations

from bot.database.database import Database
from bot.database.models import GuildConfig
from bot.utils.timezones import is_valid_timezone, parse_hhmm


def _channel_or_name(channel_id: int | None, placeholder: str) -> str:
    if channel_id:
        return f"<#{channel_id}>"
    return f"#{placeholder}"


class ConfigService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, guild_id: int) -> GuildConfig:
        return await self.db.ensure_guild(guild_id)

    def build_public_help(self, config: GuildConfig) -> str:
        quotes = _channel_or_name(config.quotes_channel_id, "цитаты")
        fest = _channel_or_name(config.fest_channel_id, "кинофестиваль")
        events = _channel_or_name(config.events_channel_id, "ивенты")
        birthday = _channel_or_name(config.birthday_channel_id, "дни-рождения")
        tgk = _channel_or_name(config.tgk_channel_id, "тгк")
        santa = _channel_or_name(config.santa_channel_id, "тайный-санта")
        return (
            "## Цитаты\n"
            f"Канал: {quotes}\n"
            "• `/quote add` — добавить цитату\n"
            "• `/quote edit` — изменить свою\n"
            "• `/quote delete` — удалить свою\n"
            "• `/quote random` — случайная\n"
            "• `/quote user` — цитаты участника\n"
            "• ПКМ по сообщению → Apps → Add quote\n"
            "\n"
            "## Кинофестиваль\n"
            f"Канал: {fest}\n"
            "• Кнопки на карточке: предложить / убрать фильм, оценка\n"
            "• `/fest new` · `/fest edit` · `/fest delete` · `/fest winner` · `/fest export`\n"
            "\n"
            "## Ивенты\n"
            f"Канал: {events}\n"
            "• `/event create` — создать (с ролью для пинга)\n"
            "• `/event cancel` · `/event ended`\n"
            "• Кнопки на карточке: присоединиться / покинуть\n"
            "\n"
            "## Дни рождения\n"
            f"Канал: {birthday}\n"
            "• Кнопки на доске: **Добавить ДР** / **Убрать ДР**\n"
            "\n"
            "## ТГК\n"
            f"Канал: {tgk}\n"
            "• Кнопки на доске: добавить / убрать телеграм-канал\n"
            "\n"
            "## Таро\n"
            "• `/tarot` — карта дня\n"
            "• `/tarot-shuffle` — перемешать колоду\n"
            "\n"
            "## Роли и статистика\n"
            "• `/myrole edit` · `/myrole delete` — персональная роль\n"
            "• `/stats profile` · `/stats top`\n"
            "\n"
            "## Тайный Санта\n"
            f"Канал: {santa}\n"
            "• Кнопка на карточке — записаться\n"
            "• `/santa start` — розыгрыш\n"
            "\n"
            "## Войс\n"
            "• `/voice toggle` — Ерунда не слышит / снова слышит тебя"
        )

    async def set_channel(
        self,
        guild_id: int,
        field: str,
        channel_id: int | None,
    ) -> GuildConfig:
        allowed = {
            "birthday_channel_id",
            "events_channel_id",
            "quotes_channel_id",
            "fest_channel_id",
            "tgk_channel_id",
            "santa_channel_id",
        }
        if field not in allowed:
            raise ValueError(f"Invalid channel field: {field}")
        return await self.db.update_guild(guild_id, **{field: channel_id})

    async def set_flag(self, guild_id: int, field: str, enabled: bool) -> GuildConfig:
        allowed = {
            "statistics_enabled",
            "personal_roles_enabled",
            "fest_presence_check",
            "tarot_show_meaning",
            "voice_chat_transcripts",
        }
        if field not in allowed:
            raise ValueError(f"Invalid flag field: {field}")
        return await self.db.update_guild(guild_id, **{field: int(enabled)})

    async def set_role(self, guild_id: int, field: str, role_id: int | None) -> GuildConfig:
        allowed = {"fest_ping_role_id"}
        if field not in allowed:
            raise ValueError(f"Invalid role field: {field}")
        return await self.db.update_guild(guild_id, **{field: role_id})

    async def set_timezone(self, guild_id: int, timezone: str) -> GuildConfig:
        if not is_valid_timezone(timezone):
            raise ValueError(f"Unknown timezone: {timezone}")
        return await self.db.update_guild(guild_id, timezone=timezone)

    async def set_birthday_announce_time(self, guild_id: int, value: str) -> GuildConfig:
        if parse_hhmm(value) is None:
            raise ValueError("Time must be HH:MM (24h)")
        return await self.db.update_guild(guild_id, birthday_announce_time=value)

    async def set_int(self, guild_id: int, field: str, value: int) -> GuildConfig:
        bounds: dict[str, tuple[int, int]] = {
            "birthday_reminder_days": (0, 30),
            "fest_reminder_minutes": (0, 10080),
            "tarot_reset_minutes": (1, 1440),
        }
        if field not in bounds:
            raise ValueError(f"Invalid int field: {field}")
        lo, hi = bounds[field]
        if not (lo <= value <= hi):
            raise ValueError(f"{field} must be between {lo} and {hi}")
        return await self.db.update_guild(guild_id, **{field: value})
