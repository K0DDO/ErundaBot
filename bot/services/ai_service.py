"""Text generation + STT via OpenRouter (primary) and Groq (fallback)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import urllib.error
import urllib.request

from bot.database.models import Birthday

log = logging.getLogger(__name__)


class OpenRouterCreditsExhausted(Exception):
    """OpenRouter returned 402 — balance/limit exhausted."""


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TRANSCRIBE_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_OPENROUTER_CHAT = "google/gemini-2.5-flash"
DEFAULT_OPENROUTER_STT = "microsoft/mai-transcribe-2"
DEFAULT_OPENROUTER_STT_FALLBACK = "openai/gpt-4o-mini-transcribe"
DEPRECATED_GROQ_MODELS = {"llama-3.1-8b-instant"}
USER_AGENT = "ErundaBot/1.0"
GROQ_TIMEOUT_SECONDS = 120
CHAT_TIMEOUT_SECONDS = 90
STT_TIMEOUT_SECONDS = 60
FILM_TITLE_TIMEOUT_SECONDS = 20
STT_KEYWORD_PHRASES = (
    "six seven",
    "6-7",
    "67",
    "сикс севен",
    "сиксевен",
    "секс севен",
    "шесть семь",
    "блядь",
    "блять",
    "нахуй",
    "сука",
    "пиздец",
    "ебать",
    "хуй",
)
DOSSIER_PROMPT = (
    "Ты помощник Discord-бота. По дневным репликам человека "
    "(текст из чатов и войса, сгруппированные по каналам) "
    "выпиши до 5 коротких нейтральных фактов для «дела» "
    "(привычки, шутки, темы, интересы). "
    "Бери ТОЛЬКО то, что явно сказано — ничего не выдумывай. "
    "Не смешивай разные каналы в один факт, если контекст разный. "
    "Игнорируй мусор STT: «спасибо», thank you, субтитры, продолжение следует, "
    "toot, обрывки без смысла, чистые мемы вроде 6-7 / six seven без контекста. "
    "Без матов, оскорблений, NSFW, политики и личных тайн. "
    "Каждый факт — одно короткое предложение на русском. "
    "Если полезного нет — верни пустой JSON-массив []. "
    "Ответ ТОЛЬКО JSON: [\"факт1\", \"факт2\"]"
)
DOSSIER_PRUNE_PROMPT = (
    "Ты помощник Discord-бота. Ниже список фактов «дела» про человека с Discord-сервера. "
    "Оставь до 10 самых ярких, устойчивых и полезных фактов. "
    "Убери дубли, устаревшее, банальное и мусор. "
    "Не выдумывай новых фактов — только отбери и слегка подчисти формулировки. "
    "Без матов, NSFW, политики и личных тайн. "
    "Ответ ТОЛЬКО JSON-массив строк: [\"факт1\", \"факт2\"]"
)
LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{2,}\b")
SIGN_OFF_RE = re.compile(
    r"(?im)^\s*(sincerely|с уважением|с любовью|your friends|друзья сервера).*$"
)
FILM_TITLE_PROMPT = (
    "Ты помогаешь найти реальный фильм по кривому пользовательскому названию.\n"
    "Верни ТОЛЬКО JSON одной строкой: "
    '{"ru":"официальное русское название","en":"official English title"} '
    "или {\"ru\":null,\"en\":null} если это не фильм / не знаешь.\n"
    "Исправь опечатки и порядок слов. Не выдумывай несуществующие фильмы.\n"
    "Примеры:\n"
    'Александр Сергеевич Пушкин Пророк -> '
    '{"ru":"Пророк. История Александра Пушкина","en":"The Prophet"}\n'
    'дедпул 1 -> {"ru":"Дэдпул","en":"Deadpool"}\n'
    'человек паук новый день -> '
    '{"ru":"Человек-паук: Новый день","en":"Spider-Man: Brand New Day"}'
)
VIBES = (
    "тёплое и простое",
    "дружеское, с лёгкой улыбкой",
    "с одной доброй шуткой",
    "искреннее, без пафоса",
)
SYSTEM_PROMPT = (
    "Ты пишешь поздравление с днём рождения в общий чат Discord от лица друзей с сервера.\n"
    "Пиши грамотным живым русским, как нормальный человек пишет другу: "
    "понятно, тепло, без странных метафор и обрывков фраз.\n"
    "2–3 коротких предложения. Каждое предложение законченное и понятное само по себе. "
    "Максимум один эмодзи в конце или ни одного.\n"
    "Обращайся по имени в начале. Без подписи, без названия сервера, без <@id>, без латиницы.\n"
    "Не выдумывай факты. Возраст упоминай, только если он дан. "
    "Из списка фактов можно мягко упомянуть один, своими словами и без цифр статистики; можно ни одного.\n"
    "Не оскорбляй, не шути про возраст как про старость, смерть, политику и NSFW.\n"
    "Избегай штампов: «пусть сбудутся мечты», «здоровья и счастья», «новых свершений», "
    "«незабываемый день», «заряд энергии», «успехов во всём», «оставайся таким же».\n\n"
    "Примеры тона (не копируй дословно):\n"
    "— Вася, с днём рождения! Спасибо, что с тобой в войсе всегда весело. Хорошего дня и вкусного торта 🎂\n"
    "— Маша, с днюхой! Пусть сегодня всё складывается легко, а вечером тебя ждут любимые люди.\n"
    "— Дима, поздравляем! Твои цитаты давно стали частью сервера, так что сегодня ты главный герой.\n"
)


class AIService:
    def __init__(self) -> None:
        self.api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        raw_groq = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
        self.groq_model = (
            DEFAULT_GROQ_MODEL if raw_groq in DEPRECATED_GROQ_MODELS else raw_groq
        )
        self.chat_model = (
            os.getenv("OPENROUTER_CHAT_MODEL", DEFAULT_OPENROUTER_CHAT).strip()
            or DEFAULT_OPENROUTER_CHAT
        )
        # Backward-compat alias used by older callers / logs.
        self.model = self.chat_model
        self.stt_model = (
            os.getenv("OPENROUTER_STT_MODEL", DEFAULT_OPENROUTER_STT).strip()
            or DEFAULT_OPENROUTER_STT
        )
        self.stt_fallback_model = (
            os.getenv(
                "OPENROUTER_STT_FALLBACK_MODEL",
                DEFAULT_OPENROUTER_STT_FALLBACK,
            ).strip()
            or DEFAULT_OPENROUTER_STT_FALLBACK
        )
        self._stt_credits_exhausted = False

    @property
    def enabled(self) -> bool:
        return bool(self.openrouter_api_key or self.api_key)

    @property
    def transcription_enabled(self) -> bool:
        return bool(self.openrouter_api_key) and not self._stt_credits_exhausted

    @property
    def stt_credits_exhausted(self) -> bool:
        return self._stt_credits_exhausted

    def mark_stt_credits_exhausted(self) -> None:
        if not self._stt_credits_exhausted:
            log.warning("OpenRouter STT credits exhausted (402) — voice listen disabled")
        self._stt_credits_exhausted = True

    def clear_stt_credits_exhausted(self) -> None:
        """Call after topping up credits if you want listen without restart."""
        self._stt_credits_exhausted = False

    @property
    def _is_reasoning_model(self) -> bool:
        return "gpt-oss" in self.chat_model or "gpt-oss" in self.groq_model

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
                f"Настроение: {vibe}\n"
                f"Факты о человеке (по желанию, максимум один):\n{fact_lines}\n"
                "Напиши только текст поздравления."
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            try:
                text = await self._chat(
                    messages,
                    temperature=0.8,
                    max_tokens=280,
                    timeout=GROQ_TIMEOUT_SECONDS,
                )
                cleaned = self._sanitize_congrats(
                    text,
                    allowed=display_name,
                    extra_allowed=[*(facts or []), *(extra_allowed or [])],
                )
                if cleaned:
                    return cleaned
                log.warning("Birthday congrats empty or dropped, retrying")
            except TimeoutError:
                log.warning("Birthday generation timed out after %ss", GROQ_TIMEOUT_SECONDS)
                return None
            except (urllib.error.URLError, OSError) as exc:
                last_error = exc
                log.warning("Birthday request failed, retrying: %s", exc)
                continue
            except Exception:
                log.exception("Birthday generation failed")
                return None
        if last_error is not None:
            log.warning("Birthday generation gave up after wait: %s", last_error)
        return None

    async def canonicalize_film_title(self, title: str) -> list[str]:
        """Return RU/EN aliases for TMDB search. Empty if AI unavailable."""
        cleaned = " ".join((title or "").split())
        if not cleaned or not self.enabled:
            return []
        messages = [
            {"role": "system", "content": FILM_TITLE_PROMPT},
            {"role": "user", "content": cleaned},
        ]
        try:
            text = await self._chat(
                messages,
                temperature=0.1,
                max_tokens=120,
                timeout=FILM_TITLE_TIMEOUT_SECONDS,
            )
        except Exception:
            log.exception("Film title canonicalize failed for %r", cleaned)
            return []
        return self._parse_film_titles(text, original=cleaned)

    @staticmethod
    def _parse_film_titles(text: str | None, *, original: str) -> list[str]:
        if not text:
            return []
        raw = text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        original_key = original.casefold().replace("ё", "е")
        ordered: list[str] = []
        for key in ("ru", "en"):
            value = data.get(key)
            if not isinstance(value, str):
                continue
            title = " ".join(value.split()).strip(" \"'")
            if not title:
                continue
            if title.casefold().replace("ё", "е") == original_key:
                continue
            if title not in ordered:
                ordered.append(title)
        cyrillic = [item for item in ordered if re.search(r"[а-яё]", item, re.IGNORECASE)]
        latin = [item for item in ordered if item not in cyrillic]
        return [*cyrillic, *latin]

    async def transcribe_wav(
        self,
        wav_bytes: bytes,
        *,
        language: str | None = "ru",
        model: str | None = None,
    ) -> str | None:
        if not self.transcription_enabled or not wav_bytes:
            return None
        try:
            return await asyncio.to_thread(
                self._request_openrouter_transcribe,
                wav_bytes,
                language,
                model or self.stt_model,
            )
        except OpenRouterCreditsExhausted:
            self.mark_stt_credits_exhausted()
            raise
        except Exception:
            log.exception("OpenRouter transcription failed model=%s", model or self.stt_model)
            return None

    async def transcribe_wav_fallback(
        self,
        wav_bytes: bytes,
        *,
        language: str | None = "ru",
    ) -> str | None:
        """Second STT pass (GPT-4o mini transcribe) when primary mangled the clip."""
        if self.stt_fallback_model == self.stt_model:
            return await self.transcribe_wav(wav_bytes, language=language)
        return await self.transcribe_wav(
            wav_bytes,
            language=language,
            model=self.stt_fallback_model,
        )

    def _request_openrouter_transcribe(
        self,
        wav_bytes: bytes,
        language: str | None = "ru",
        model: str | None = None,
    ) -> str:
        chosen = model or self.stt_model
        body: dict = {
            "model": chosen,
            "response_format": "json",
            "prompt": (
                "six seven, 6-7, 67, сикс севен, сек севен, сексаван, сексеон, "
                "sex seven, sick seven, шесть семь. "
                "блядь, блять, нахуй, сука, пиздец, ебать, хуй."
            ),
            "input_audio": {
                "data": base64.b64encode(wav_bytes).decode("ascii"),
                "format": "wav",
            },
        }
        if language:
            body["language"] = language
        if "mai-transcribe" in chosen:
            body["provider"] = {
                "options": {
                    "azure": {
                        "phraseList": {"phrases": list(STT_KEYWORD_PHRASES)},
                    }
                }
            }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            OPENROUTER_TRANSCRIBE_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": USER_AGENT,
                "HTTP-Referer": "https://github.com/K0DDO/ErundaBot",
                "X-Title": "Erunda Discord Bot",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=STT_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:500]
            log.error("OpenRouter STT HTTP %s model=%s: %s", exc.code, chosen, body_text)
            if exc.code == 402:
                raise OpenRouterCreditsExhausted(
                    "Токены OpenRouter исчерпаны (402)"
                ) from exc
            raise
        text = data.get("text") if isinstance(data, dict) else None
        return str(text or "").strip()

    async def summarize_voice_dossier(
        self,
        *,
        display_name: str,
        lines: str,
    ) -> list[str]:
        return await self.summarize_daily_dossier(
            display_name=display_name,
            lines=lines,
        )

    async def summarize_daily_dossier(
        self,
        *,
        display_name: str,
        lines: str,
    ) -> list[str]:
        if not self.enabled or not lines.strip():
            return []
        messages = [
            {"role": "system", "content": DOSSIER_PROMPT},
            {
                "role": "user",
                "content": f"Имя: {display_name}\nРеплики за день:\n{lines[:12000]}",
            },
        ]
        try:
            raw = await self._chat(
                messages,
                temperature=0.3,
                max_tokens=500,
                timeout=60,
            )
        except Exception:
            log.exception("Daily dossier summarize failed")
            return []
        return self._parse_fact_list(raw)

    async def prune_dossier_facts(
        self,
        *,
        display_name: str,
        facts: list[str],
        keep: int = 10,
    ) -> list[str]:
        cleaned = [item.strip() for item in facts if item and item.strip()]
        if not cleaned:
            return []
        if len(cleaned) <= keep:
            return cleaned[:keep]
        if not self.enabled:
            return cleaned[:keep]
        joined = "\n".join(f"- {item}" for item in cleaned[:80])
        messages = [
            {"role": "system", "content": DOSSIER_PRUNE_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Имя: {display_name}\nОставь до {keep} лучших:\n{joined}"
                ),
            },
        ]
        try:
            raw = await self._chat(
                messages,
                temperature=0.2,
                max_tokens=600,
                timeout=60,
            )
        except Exception:
            log.exception("Dossier prune failed")
            return cleaned[:keep]
        pruned = self._parse_fact_list(raw)
        return (pruned or cleaned)[:keep]

    @staticmethod
    def _parse_fact_list(raw: str) -> list[str]:
        text = (raw or "").strip()
        if not text:
            return []
        try:
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                text = text[start : end + 1]
            data = json.loads(text)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        junk = re.compile(
            r"(?i)dimatorzok|субтитры|продолжение\s+следует|благодар|"
            r"pewds|следующ(?:ему|ее)\s+видео|непонятн|"
            r"говорит на русском|разговорный русский|thank\s*you"
        )
        facts: list[str] = []
        for item in data:
            if isinstance(item, str):
                cleaned = " ".join(item.split()).strip()
                if cleaned and not junk.search(cleaned):
                    facts.append(cleaned[:240])
        return facts[:10]

    async def _chat(
        self,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str:
        errors: list[str] = []
        if self.openrouter_api_key:
            try:
                return await asyncio.to_thread(
                    self._request_openrouter_chat,
                    messages,
                    self.chat_model,
                    temperature,
                    max_tokens,
                    timeout,
                )
            except Exception as exc:
                errors.append(f"openrouter:{exc}")
                log.warning(
                    "OpenRouter chat failed model=%s, falling back to Groq: %s",
                    self.chat_model,
                    exc,
                )
        if self.api_key:
            try:
                return await asyncio.to_thread(
                    self._request_groq_chat,
                    messages,
                    self.groq_model,
                    temperature,
                    max_tokens,
                    timeout,
                )
            except Exception as exc:
                errors.append(f"groq:{exc}")
                log.exception("Groq chat failed model=%s", self.groq_model)
                raise
        raise RuntimeError("; ".join(errors) or "no chat backend configured")

    def _request_openrouter_chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str:
        body: dict = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            OPENROUTER_CHAT_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": USER_AGENT,
                "HTTP-Referer": "https://github.com/K0DDO/ErundaBot",
                "X-Title": "Erunda Discord Bot",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:400]
            log.error("OpenRouter chat HTTP %s: %s", exc.code, body_text)
            if exc.code == 402:
                raise OpenRouterCreditsExhausted(
                    "Токены OpenRouter исчерпаны (402)"
                ) from exc
            raise
        message = (data.get("choices") or [{}])[0].get("message") or {}
        return self._message_text(message)

    def _request_groq_chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> str:
        body: dict = {
            "model": model,
            "temperature": temperature,
            "messages": messages,
        }
        if "gpt-oss" in model:
            body["max_completion_tokens"] = max(max_tokens, 1024)
            body["reasoning_effort"] = "low"
        else:
            body["max_tokens"] = max_tokens
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
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
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:400]
            log.error("Groq HTTP %s: %s", exc.code, body_text)
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
