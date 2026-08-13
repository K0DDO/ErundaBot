"""Groq-backed text generation for birthday congratulations."""

from __future__ import annotations

import logging
import os

import httpx

from bot.database.models import Birthday

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM_PROMPT = (
    "Ты пишешь короткие тёплые поздравления с днём рождения для Discord-сервера. "
    "Язык: русский. Тон: дружеский, чуть ироничный, без канцелярита. "
    "2–4 предложения. Можно использовать эмодзи. "
    "Не оскорбляй, не шути про смерть, политику и NSFW. "
    "Не выдумывай факты о человеке. Если возраст неизвестен — не упоминай возраст. "
    "В тексте обязательно обратись к человеку по имени, без Discord-упоминаний <@id>."
)


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def generate_birthday_congrats(
        self,
        *,
        guild_name: str,
        display_name: str,
        birthday: Birthday,
        today,
    ) -> str | None:
        if not self.enabled:
            return None
        from bot.services.birthday_service import age_on, format_birthday_date

        age = age_on(birthday, today)
        date_text = format_birthday_date(birthday.day, birthday.month, birthday.year)
        age_line = f"Сегодня исполняется {age} лет." if age is not None else "Возраст неизвестен."
        user_prompt = (
            f"Сервер: {guild_name}\n"
            f"Имя: {display_name}\n"
            f"Дата: {date_text}\n"
            f"{age_line}\n"
            "Напиши одно поздравление."
        )
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "temperature": 0.9,
                        "max_tokens": 220,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
            response.raise_for_status()
            data = response.json()
            text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            return text or None
        except Exception:
            log.exception("Groq birthday generation failed")
            return None

    async def build_announce_embed_description(
        self,
        guild,
        birthday: Birthday,
        today,
        *,
        mention: bool = True,
    ) -> tuple[str, bool]:
        member = guild.get_member(birthday.user_id)
        display = member.display_name if member is not None else f"участник #{birthday.user_id}"
        mention_text = f"<@{birthday.user_id}>" if mention else f"**{display}**"
        generated = await self.generate_birthday_congrats(
            guild_name=guild.name,
            display_name=display,
            birthday=birthday,
            today=today,
        )
        if generated:
            return f"{mention_text}\n\n{generated}", True
        from bot.services.birthday_service import age_on

        age = age_on(birthday, today)
        age_part = f" Исполняется {age}!" if age is not None else ""
        return (
            f"Сегодня день рождения у {mention_text}!\nПоздравляем!{age_part}",
            False,
        )
