"""Groq-backed text generation for birthday congratulations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import urllib.error
import urllib.request

from bot.database.models import Birthday

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEPRECATED_GROQ_MODELS = {"llama-3.1-8b-instant"}
USER_AGENT = "ErundaBot/1.0"
GROQ_TIMEOUT_SECONDS = 120
LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")
SIGN_OFF_RE = re.compile(
    r"(?im)^\s*(sincerely|с уважением|с любовью|your friends|друзья сервера).*$"
)
VIBES = (
    "нежное, без слащавости",
    "лёгкий подкол из войса, без злости",
    "дурацкое и тёплое",
    "пафос на одну фразу, сразу сбивается в шутку",
    "смесь тепла и подкола в одном сообщении",
)
SYSTEM_PROMPT = (
    "Ты пишешь короткое поздравление с днём рождения в чат Discord, "
    "как друг с войса, а не как бот и не как открытка.\n"
    "Только русский. Без подписи, без названия сервера, без <@id>.\n"
    "1–2 предложения, максимум три. Можно чуть криво, как в чате. Эмодзи — по желанию, не обязательно.\n"
    "Не оскорбляй, не шути про смерть, политику и NSFW. Не выдумывай факты. "
    "Возраст — только если он дан. Факты из списка можно зацепить один, не все сразу, можно ни одного.\n"
    "Нельзя штампы: «пусть сбудутся мечты», «здоровья и счастья», «новых свершений», "
    "«незабываемый день», «заряд энергии», «успехов во всём», «оставайся таким же».\n"
    "Обращайся по имени.\n\n"
    "Примеры тона (не копируй дословно):\n"
    "— Вася, с днём рождения. Камера в войс, торт потом.\n"
    "— Маша, ещё один круг. Мы это заметили, даже если ты притворишься что нет.\n"
    "— Дима, с днюхой. Цитаты твои мы и так читаем, сегодня можно просто сидеть и принимать любовь.\n"
)


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        raw_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
        self.model = DEFAULT_GROQ_MODEL if raw_model in DEPRECATED_GROQ_MODELS else raw_model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def _is_reasoning_model(self) -> bool:
        return "gpt-oss" in self.model

    async def generate_birthday_congrats(
        self,
        *,
        guild_name: str,
        display_name: str,
        birthday: Birthday,
        today,
        facts: list[str] | None = None,
        extra_allowed: list[str] | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        from bot.services.birthday_service import age_on, format_birthday_date

        age = age_on(birthday, today)
        date_text = format_birthday_date(birthday.day, birthday.month, birthday.year)
        age_line = f"Сегодня исполняется {age}." if age is not None else "Возраст неизвестен — не пиши цифру лет."
        fact_lines = "\n".join(f"- {item}" for item in (facts or []) if item) or "- нет"
        last_error: Exception | None = None
        for _attempt in range(2):
            vibe = random.choice(VIBES)
            user_prompt = (
                f"Имя: {display_name}\n"
                f"Дата: {date_text}\n"
                f"{age_line}\n"
                f"Характер этого раза: {vibe}\n"
                f"Факты, если захочешь зацепить один:\n{fact_lines}\n"
                "Напиши одно сообщение в чат. Без открытки, без канцелярита."
            )
            body: dict = {
                "model": self.model,
                "temperature": 0.95 if self._is_reasoning_model else 1.05,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if self._is_reasoning_model:
                body["max_completion_tokens"] = 2048
                body["reasoning_effort"] = "low"
            else:
                body["max_tokens"] = 280
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            try:
                text = await asyncio.to_thread(self._request_groq, payload)
                cleaned = self._sanitize_congrats(
                    text,
                    allowed=display_name,
                    extra_allowed=[*(facts or []), *(extra_allowed or [])],
                )
                if cleaned:
                    return cleaned
                log.warning("Birthday congrats empty or dropped, retrying")
            except TimeoutError:
                log.warning("Groq birthday generation timed out after %ss", GROQ_TIMEOUT_SECONDS)
                return None
            except (urllib.error.URLError, OSError) as exc:
                last_error = exc
                log.warning("Groq birthday request failed, retrying: %s", exc)
                continue
            except Exception:
                log.exception("Groq birthday generation failed")
                return None
        if last_error is not None:
            log.warning("Groq birthday generation gave up after wait: %s", last_error)
        return None

    def _request_groq(self, payload: bytes) -> str:
        request = urllib.request.Request(
            GROQ_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=GROQ_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            log.error("Groq HTTP %s: %s", exc.code, body)
            raise
        message = (data.get("choices") or [{}])[0].get("message") or {}
        return self._message_text(message)

    @staticmethod
    def _message_text(message: dict) -> str:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
            joined = "".join(parts).strip()
            if joined:
                return joined
        return ""

    @staticmethod
    def _sanitize_congrats(
        text: str | None,
        *,
        allowed: str = "",
        extra_allowed: list[str] | None = None,
    ) -> str | None:
        if not text:
            return None
        lines = [line.rstrip() for line in text.strip().splitlines()]
        while lines and SIGN_OFF_RE.match(lines[-1] or ""):
            lines.pop()
        cleaned = "\n".join(line for line in lines if line is not None).strip()
        if not cleaned:
            return None
        check = cleaned
        for token in [allowed, *(extra_allowed or [])]:
            if token:
                check = check.replace(token, "")
        if LATIN_WORD_RE.search(check):
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
        facts: list[str] | None = None,
        extra_allowed: list[str] | None = None,
    ) -> tuple[str, bool]:
        member = guild.get_member(birthday.user_id)
        display = member.display_name if member is not None else f"участник #{birthday.user_id}"
        mention_text = f"<@{birthday.user_id}>" if mention else f"**{display}**"
        generated = await self.generate_birthday_congrats(
            guild_name=guild.name,
            display_name=display,
            birthday=birthday,
            today=today,
            facts=facts,
            extra_allowed=extra_allowed,
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
