"""Groq-backed text generation for birthday congratulations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request

from bot.database.models import Birthday

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEPRECATED_GROQ_MODELS = {"llama-3.1-8b-instant"}
LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")
SIGN_OFF_RE = re.compile(
    r"(?im)^\s*(sincerely|с уважением|с любовью|your friends|друзья сервера).*$"
)
SYSTEM_PROMPT = (
    "Ты пишешь короткие тёплые поздравления с днём рождения для Discord-сервера. "
    "Пиши ТОЛЬКО на русском языке. Ни одного английского слова, даже в подписи. "
    "Запрещены: Sincerely, Happy birthday, Best wishes, Congratulations и любые латиница-слова. "
    "Не ставь подпись в конце («С уважением», «Ерундульки», имя сервера). "
    "Тон: дружеский, чуть ироничный, без канцелярита. "
    "2–4 предложения. Можно использовать эмодзи. "
    "Не оскорбляй, не шути про смерть, политику и NSFW. "
    "Не выдумывай факты о человеке. Если возраст неизвестен — не упоминай возраст. "
    "В тексте обратись к человеку по имени, без Discord-упоминаний <@id>."
)


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        raw_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
        self.model = DEFAULT_GROQ_MODEL if raw_model in DEPRECATED_GROQ_MODELS else raw_model

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
            "Напиши одно поздравление только по-русски, без английских слов и без подписи."
        )
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0.9,
                "max_tokens": 220,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        try:
            text = await asyncio.to_thread(self._request_groq, payload)
            return self._sanitize_congrats(text)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            log.exception("Groq birthday generation failed")
            return None

    def _request_groq(self, payload: bytes) -> str:
        request = urllib.request.Request(
            GROQ_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

    @staticmethod
    def _sanitize_congrats(text: str | None) -> str | None:
        if not text:
            return None
        lines = [line.rstrip() for line in text.strip().splitlines()]
        while lines and SIGN_OFF_RE.match(lines[-1] or ""):
            lines.pop()
        cleaned = "\n".join(line for line in lines if line is not None).strip()
        if not cleaned:
            return None
        if LATIN_WORD_RE.search(cleaned):
            log.warning("Dropped birthday congrats because it contained Latin words")
            return None
        return cleaned

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
