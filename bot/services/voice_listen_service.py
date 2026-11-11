"""Join voice, receive PCM, transcribe via Groq Whisper, store snippets/dossier."""

from __future__ import annotations

import asyncio
import audioop
import io
import logging
import os
import re
import tempfile
import wave
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord.opus import OpusError

from bot.database.database import Database
from bot.services.ai_service import (
    HEYGEN_VOICES,
    OpenRouterCreditsExhausted,
    OpenRouterRateLimited,
)
from bot.settings import is_home_guild, voice_listen_allowed

if TYPE_CHECKING:
    from bot.bot import ErundaBot
    from bot.services.ai_service import AIService

log = logging.getLogger(__name__)

CREDITS_EXHAUSTED_MESSAGE = (
    "Токены OpenRouter исчерпаны — слушание войса отключено. "
    "Пополни баланс и перезапусти бота (или дождись рестарта)."
)

# Discord voice PCM: 48 kHz, stereo, 16-bit LE.
PCM_RATE = 48000
PCM_CHANNELS = 2
PCM_SAMPLE_WIDTH = 2
# Wait this long after last PCM before STT+announce (was 2.0 → 0.85 → still felt ~3s to soundboard).
SILENCE_FLUSH_SECONDS = 0.4
# Short loud shouts (memes) can flush sooner — less dead air before soundboard.
SILENCE_FLUSH_MEME_SECONDS = 0.25
MEME_FLUSH_MAX_SECONDS = 3.5
MEME_FLUSH_MIN_RMS = 700
MIN_AUDIO_SECONDS = 0.45
MAX_BUFFER_SECONDS = 8.0
BYTES_PER_SECOND = PCM_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH
# Join nearby STT chunks so "сикс" + "севен" across a short pause still match.
SIXSEVEN_CONTEXT_SECONDS = 6.0
SIXSEVEN_CONTEXT_MAX_CHUNKS = 6
# Auto-recover when SSRC/speaker map or listen sink dies (STT stops).
VOICE_RECOVER_UNKNOWN_STREAK = 100
VOICE_HARD_RECOVER_UNKNOWN_STREAK = 250
VOICE_HEAL_COOLDOWN_SECONDS = 60.0
VOICE_STALE_PCM_SECONDS = 30.0
VOICE_HEALTH_CHECK_EVERY = 8.0
VOICE_SOFT_REBIND_COOLDOWN_SECONDS = 120.0
VOICE_SOFT_REBIND_ESCALATE_AFTER = 2
VOICE_SOFT_REBIND_WINDOW_SECONDS = 300.0
VOICE_SINK_DEAD_CONFIRM = 3
VOICE_HALLUCINATION_RECOVER_STREAK = 3
# After connect: wait for real RTP before trusting health checks.
VOICE_RECOVER_GRACE_SECONDS = 45.0
VOICE_RTP_WAIT_SECONDS = 20.0
VOICE_USEFUL_STT_STALE_SECONDS = 150.0
VOICE_DAVE_FAIL_WINDOW_SECONDS = 12.0
VOICE_DAVE_FAIL_RECOVER_COUNT = 40
VOICE_NO_RTP_RECOVER_SECONDS = 50.0
# Resume join only if Discord already dropped us — never leave+join while connected.
# Was 180s — felt like «вернётся через несколько минут».
VOICE_RESUME_JOIN_COOLDOWN_SECONDS = 25.0
# Connected but deaf (no PCM/callbacks): force leave+rejoin. Soft DAVE remount is unsafe;
# a true zombie session is already dead — hard resume is the recovery path.
VOICE_ZOMBIE_FORCE_RESUME_SECONDS = 90.0
# Ops visibility only — never leave+join / remount to "heal" a live DAVE session.
VOICE_HEALTH_STATUS_EVERY = 60.0
# If STT task dies/hangs without clearing flushing, unblock the user buffer.
VOICE_FLUSH_STUCK_SECONDS = 45.0
# Opus DTX silence frame (pycord / cursedshrine): keeps decoder alive on decrypt miss.
_OPUS_SILENCE = b"\xf8\xff\xfe"

# Rolling DAVE decrypt stats (updated from voice_recv patch).
_DAVE_EVENTS: list[tuple[float, bool]] = []

# RU (+ EN Whisper) swear stems; count after STT for /stats later.
# Word-start only; include common prefixes (заеб/нахуй/…) as separate alts.
SWEAR_RE = re.compile(
    r"(?i)(?<![а-яёa-z])"
    r"(?:"
    # бля*
    r"бл[яа][дт]|бляд|блят|бля|"
    # сука / сучка
    r"сука|суки|сучк|сучар|"
    # хуй*
    r"хуй|хуя|хуе|хуё|хуи|хули|хуйн|хуяк|"
    r"нахуй|нахуя|нихуя|похуй|охуе|охуи|охуел|ахуе|ахуи|охер|"
    # пизд*
    r"пизд|"
    # еб*/ёб* + prefixed
    r"ёб|еб[аоуыиё]|еба|ебл|ебан|ебуч|еблан|ебат|"
    r"заеб|наеб|уеб|выеб|разъеб|приеб|объеб|доеб|подъеб|перееб|отъеб|"
    r"долбо[её]б|у[её]бищ|"
    # муд*
    r"муд[аеи]|мудил|мудо|"
    # прочее классика
    r"гандон|гондон|залуп|"
    r"пидор|пидар|пидр|педик|педр|"
    r"хер(?![еэ])|хрен|"
    r"жоп|задниц|"
    r"дроч|конч|сперм|минет|отсос|"
    r"говн|дерьм|"
    r"ссат|срат|сран|срун|"
    r"шлюх|блядун|блядин|блядск|"
    r"чмо|мраз|тварь|падл|ублюд|выбляд|сволоч|дрян|гнид|"
    # EN + latinized RU (Whisper often transliterates)
    r"fuck|fucking|motherfucker|shit|bitch|asshole|dickhead|cunt|bastard|"
    r"blyat|blyad|blya|suka|suchka|nahui|nahuy|pizd|ebat|yobanu|huy|xyu"
    r")"
    r"[а-яёa-z]*"
)
# Whisper mangles the meme hard: «6, 7», сексон, «Зигг сервон», «сюксалон»…
_SIX_SEP = r"[\s\-–—./,;:…]*"
# First half of the meme as Whisper hears it.
_SIX_STEM = (
    r"(?:сикс|сик\s*с?|секс|сек\s*с?|сыкс|сакс|сюкс|сукс|сухс|цикс|сигг?|"
    r"six|sick|sik|sex|see|zick|zigg?|zik|"
    r"зигг+|зиг|зик)"
)
# Second half.
_SEVEN_STEM = (
    r"(?:севен|севон|сервон|серван|саван|сайон|саён|салон|семь|"
    r"seven|sevon|servon|servan|sevan|sion|salon)"
)
SIXSEVEN_RE = re.compile(
    r"(?:"
    rf"six{_SIX_SEP}seven|"
    rf"sick{_SIX_SEP}seven|"
    rf"see{_SIX_SEP}seven|"
    rf"c{_SIX_SEP}seven|"
    r"six\s*7|"
    rf"6{_SIX_SEP}seven|"
    rf"sex{_SIX_SEP}seven|"
    r"sexon|sixon|sexion|sixion|"
    rf"(?<!\d)6{_SIX_SEP}7(?!\d)|"
    rf"сикс{_SIX_SEP}севен|"
    rf"сик\s*с?{_SIX_SEP}севен|"
    r"сикс[аеэ]?в[аеэ]?н|"
    r"сикс[ао]?в[аеэ]н|"
    # Whisper: «Сексеон», «сексион», «сиксеон»…
    r"сикс[еёиыао]?[оае]?н|"
    # Live 2026-11-08: «Суксал», «Сюксалон», «Сексуя», «сухсад»
    r"с[июуы]кс[аеоуыи]?л?[аоеу]?н?|"
    r"с[ею]кс[уиы][яею]|"
    r"сухс[аеоу]д|сукс[аеоу]д|"
    rf"сек\s*с?{_SIX_SEP}севен|"
    rf"секс{_SIX_SEP}севен|"
    r"секс[ао]?в[аеэ]н|"
    r"сексев[аеэ]н|"
    r"секс[еёиыао]?[оае]?н|"
    r"сексон|циксон|сиксон|сексан|сексеон|сексион|сиксеон|"
    # Whisper: «Секфа он.» (2026-11-07)
    r"секфа\s*он|секфаон|сикфа\s*он|сикфаон|"
    r"экс[еэ]в[еэ]н|exseven|x\s*seven|"
    rf"сыкс{_SIX_SEP}севен|"
    rf"сакс{_SIX_SEP}севен|"
    rf"шесть{_SIX_SEP}семь|"
    rf"6{_SIX_SEP}семь|"
    rf"6{_SIX_SEP}саван|"
    r"шесть\s*7|"
    # Live Whisper mangling observed 2026-11-07: «Зигг, сервон!» / «Zick, servon!»
    rf"{_SIX_STEM}{_SIX_SEP}{_SEVEN_STEM}"
    r")"
)
# Whisper noise / music hallucinations on bad / encrypted-as-noise audio.
JUNK_TRANSCRIPT_RE = re.compile(
    r"(?is)^(?:[\s\-–—*.,…·•]+|\*[^*]{0,40}\*|♪+|\[.*?\]|\(.*?\))$"
)
# Classic Whisper hallucinations when PCM is DAVE-ciphertext noise.
HALLUCINATION_RE = re.compile(
    r"(?i)"
    r"субтитры\s+(?:сделал|создавал)|продолжение\s+следует|dimatorzok|"
    r"редактор\s+субтитров|коррек(?:тор|ция)\s+субтитров|"
    r"thanks?\s+for\s+watching|subscribe|like\s+and\s+subscribe|"
    r"amara\.org|translated\s+by|caption(s|ed)?\s+by|"
    r"toot\s+toot|pewds|"
    # Empty-room / noise often becomes only thanks spam (RU/EN).
    r"^(?:(?:спасибо|thanks?|thank\s+you|ty)[.!,…]*\s*)+$"
)
# Filler that should never enter voice «дело».
DOSSIER_JUNK_RE = re.compile(
    r"(?i)"
    r"субтитры|продолжение\s+следует|dimatorzok|"
    r"thank\s+you|thanks\s+for|toot\s+toot|"
    r"редактор\s+субтитров|коррек(?:тор|ция)"
)
SOUNDBOARD_SIXSEVEN_NAME = "67"
SOUNDBOARD_ERUNDA_NAME = "уц"
# Back-compat alias used in older log strings / tips.
SOUNDBOARD_TRIGGER_NAME = SOUNDBOARD_SIXSEVEN_NAME
SOUNDBOARD_COOLDOWN_SECONDS = 2.0
# Seasonal greeting for @i_aisa (HeyGen Gareth) until this Moscow date inclusive.
AISA_USERNAME = "i_aisa"
AISA_GREETING_UNTIL = date(2026, 11, 30)
AISA_GREETING_TEXT = "здравствуйте госпожа"
AISA_GREETING_VOICE = "gareth"
AISA_GREETING_DELAY_SECONDS = 2.0
AISA_GREETING_COOLDOWN_SECONDS = 20.0
AISA_TTS_CACHE_PATH = Path("data/tts_cache/aisa_gareth.pcm")
# 16-bit PCM RMS below this ≈ silence / decrypt garbage.
MIN_PCM_RMS = 250
_VOICE_RECV_PATCHED = False

# «ерунда*» + Whisper latin/mangling (ерундушка, erunda, yerunda…).
ERUNDA_RE = re.compile(
    r"(?:"
    r"ерунд[а-яё]*|"
    r"ерунта[а-яё]*|"
    r"ирунд[а-яё]*|"
    r"ярунд[а-яё]*|"
    r"erund[a-z]*|"
    r"yerund[a-z]*|"
    r"ierund[a-z]*|"
    r"irund[a-z]*"
    r")"
)


def count_swears(text: str) -> int:
    return len(SWEAR_RE.findall(text or ""))


def _normalize_stt_text(text: str) -> str:
    cleaned = (text or "").casefold().replace("ё", "е")
    cleaned = re.sub(r"[\"'«»„“”!?]", " ", cleaned)
    return cleaned


def _normalize_sixseven_text(text: str) -> str:
    cleaned = _normalize_stt_text(text)
    # Whisper often sticks punctuation to digits/words: «6-7.», «6, 7».
    cleaned = re.sub(r"(?<!\d)6[\s\-–—./,;:…]+7(?!\d)", "67", cleaned)
    # Collapse observed RU/EN mangling toward canonical stems before regex.
    replacements = (
        (r"зигг+", "сикс"),
        (r"\bзиг\b", "сикс"),
        (r"\bзик\b", "сикс"),
        (r"\bсигг+\b", "сикс"),
        (r"\bсиг\b", "сикс"),
        (r"сюкс", "сикс"),
        (r"сукс", "сикс"),
        (r"сухс", "сикс"),
        (r"\bzick\b", "six"),
        (r"\bzigg+\b", "six"),
        (r"\bzig\b", "six"),
        (r"\bzik\b", "six"),
        (r"сервон", "севен"),
        (r"серван", "севен"),
        (r"севон", "севен"),
        (r"сайон", "севен"),
        (r"саён", "севен"),
        (r"салон", "севен"),
        (r"сексуя+", "сексон"),
        (r"сексуй", "сексон"),
        (r"\bservon\b", "seven"),
        (r"\bservan\b", "seven"),
        (r"\bsevon\b", "seven"),
        (r"\bsevan\b", "seven"),
        (r"\bsion\b", "seven"),
        (r"\bsalon\b", "seven"),
    )
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def mentions_sixseven(text: str) -> bool:
    cleaned = _normalize_sixseven_text(text)
    if SIXSEVEN_RE.search(cleaned):
        return True
    # Compact one-token shout: «сюксалонсюксалон» / «суксал.»
    compact = re.sub(r"[^a-zа-я0-9]+", "", cleaned)
    if not compact:
        return False
    return bool(
        re.search(
            r"(?:"
            r"с[июуы]кс[аеоуыилнд]{1,6}|"
            r"с[еэ]кс[уиы][яею]|"
            r"сикс[аеоу]?л?[аоеу]?н|"
            r"сексон|сиксон|сексеон|"
            r"sixseven|sexseven|sexon"
            r")",
            compact,
        )
    )


def maybe_sixseven_near_miss(text: str) -> bool:
    """Cheap hint that Whisper mangled the meme — worth a no-lang retry."""
    cleaned = _normalize_sixseven_text(text)
    if not cleaned or mentions_sixseven(cleaned):
        return False
    return bool(
        re.search(
            rf"(?:{_SIX_STEM}|{_SEVEN_STEM}|шесть|67|сюкс|сукс|сухс|сексу|"
            r"(?<!\d)6\D{0,4}7(?!\d)|(?<!\d)6\b)",
            cleaned,
        )
    )


def mentions_erunda(text: str) -> bool:
    """True for ерунда / ерунды / ерундушка / erunda / …"""
    cleaned = _normalize_stt_text(text)
    if ERUNDA_RE.search(cleaned):
        return True
    compact = re.sub(r"[^a-zа-я0-9]+", "", cleaned)
    return bool(compact) and bool(ERUNDA_RE.search(compact))


def maybe_erunda_near_miss(text: str) -> bool:
    cleaned = _normalize_stt_text(text)
    if not cleaned or mentions_erunda(cleaned):
        return False
    # Partial stems Whisper often produces before a clean «ерунда».
    return bool(
        re.search(
            r"(?:ерун|ерум|ерунд|erund|yerund|ирунд|ярунд)",
            cleaned,
        )
    )


def is_hallucination_heavy(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if HALLUCINATION_RE.search(cleaned):
        return True
    return False


_HALLUCINATION_PHRASE_RE = re.compile(
    r"(?i)"
    r"(?:субтитры\s+(?:сделал|создавал)\s*\S*)|"
    r"(?:продолжение\s+следует[.…]*)|"
    r"(?:редактор\s+субтитров[^.…]*)|"
    r"(?:коррек(?:тор|ция)\s+субтитров[^.…]*)|"
    r"(?:dimatorzok)|"
    r"(?:thanks?\s+for\s+watching[^.…]*)|"
    r"(?:toot(?:\s+toot)*)"
)


def strip_hallucination_phrases(text: str) -> str:
    """Keep real speech when Whisper prepends subtitle junk."""
    cleaned = _HALLUCINATION_PHRASE_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" .-–—,;:")
    return cleaned


def _cyrillic_letters(text: str) -> int:
    return sum(
        1
        for ch in text
        if ("а" <= ch.lower() <= "я") or ch.lower() == "ё"
    )


def is_useful_transcript(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 2:
        return False
    if JUNK_TRANSCRIPT_RE.match(cleaned):
        return False
    if HALLUCINATION_RE.search(cleaned):
        return False
    # Whole-utterance filler only (substring «thank you» must not kill swears).
    if re.fullmatch(
        r"(?i)(?:(?:спасибо|thanks?|thank\s+you|ty)[.!,…]*\s*)+",
        cleaned,
    ):
        return False
    if re.fullmatch(r"(?i)(?:toot\s*)+", cleaned):
        return False
    letters = sum(1 for ch in cleaned if ch.isalpha())
    if letters < 2:
        return False
    # Latin-only filler from no-lang Whisper on RU VC noise.
    if _cyrillic_letters(cleaned) == 0 and letters < 12:
        return False
    return True


def is_dossier_worthy(text: str) -> bool:
    """Snippets safe to feed into daily «дело» summarizer."""
    cleaned = (text or "").strip()
    if not is_useful_transcript(cleaned):
        return False
    if DOSSIER_JUNK_RE.search(cleaned):
        return False
    if _cyrillic_letters(cleaned) < 6:
        return False
    letters = sum(1 for ch in cleaned if ch.isalpha())
    if letters < 10:
        return False
    # Lone meme trigger is not a personality fact.
    if mentions_sixseven(cleaned) and letters < 24:
        return False
    if mentions_erunda(cleaned) and letters < 24:
        return False
    return True


def pcm_rms(pcm: bytes) -> int:
    if len(pcm) < 4:
        return 0
    try:
        return int(audioop.rms(pcm, PCM_SAMPLE_WIDTH))
    except Exception:
        return 0


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    # Whisper is happier with mono.
    try:
        mono = audioop.tomono(pcm, PCM_SAMPLE_WIDTH, 0.5, 0.5)
    except Exception:
        mono = pcm
        channels = PCM_CHANNELS
    else:
        channels = 1
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(PCM_SAMPLE_WIDTH)
        wav.setframerate(PCM_RATE)
        wav.writeframes(mono)
    return buf.getvalue()


def _note_dave_result(ok: bool) -> None:
    now = datetime.now(timezone.utc).timestamp()
    _DAVE_EVENTS.append((now, ok))
    # Keep ~30s of history.
    cutoff = now - 30.0
    while _DAVE_EVENTS and _DAVE_EVENTS[0][0] < cutoff:
        _DAVE_EVENTS.pop(0)


def _dave_fail_counts(*, window: float = VOICE_DAVE_FAIL_WINDOW_SECONDS) -> tuple[int, int]:
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - window
    fails = 0
    oks = 0
    for ts, ok in _DAVE_EVENTS:
        if ts < cutoff:
            continue
        if ok:
            oks += 1
        else:
            fails += 1
    return fails, oks


def _dave_cryptor_broken() -> bool:
    fails, oks = _dave_fail_counts()
    return fails >= VOICE_DAVE_FAIL_RECOVER_COUNT and fails > oks * 3


def _reset_dave_stats() -> None:
    _DAVE_EVENTS.clear()


def _strip_rtp_padding(raw: bytes, *, padding: bool = False) -> bytes:
    """Strip RTP padding only when the P-bit is set (RFC 3550 §5.1).

    Heuristic stripping of trailing identical bytes corrupts DAVE ciphertext
    (pycord#3386 / davey#15) and produces NoValidCryptorFound storms.
    """
    if not raw or not padding:
        return raw
    n = raw[-1]
    if 0 < n < len(raw):
        return raw[:-n]
    return raw


def _strip_dave_passthrough_trailers(raw: bytes, *, padding: bool = False) -> bytes:
    """Recover Opus from Discord passthrough frames (pycord PR#3201).

    Layout: [raw_opus][dave_supp_block][rtp_padding]
    supp_block ends with supp_size(1) + 0xFAFA(2); FAFA is NOT ciphertext here.
    """
    opus = _strip_rtp_padding(raw, padding=padding)
    if len(opus) >= 3 and opus[-2:] == b"\xfa\xfa":
        supp_size = opus[-3]
        if 3 <= supp_size < len(opus):
            opus = opus[:-supp_size]
    return opus


def _patch_voice_recv() -> None:
    """DAVE decrypt + OpusError survival without leave/join or sink remount.

    Critical: decrypt with the SSRC→user mapping only. Brute-forcing every MLS
    user_id poisons davey nonce/ratchet state (NoValidCryptorFound storms) and
    the bot goes silently deaf while RTP still flows.
    """
    global _VOICE_RECV_PATCHED
    if _VOICE_RECV_PATCHED:
        return

    from davey import MediaType
    from discord.ext.voice_recv import opus as vr_opus
    from discord.ext.voice_recv import router as vr_router
    from discord.opus import Decoder as OpusDecoder

    try:
        from discord.ext.voice_recv.rtp import OPUS_SILENCE as _VR_OPUS_SILENCE

        opus_silence = bytes(_VR_OPUS_SILENCE)
    except Exception:
        opus_silence = _OPUS_SILENCE

    orig_init = vr_opus.PacketDecoder.__init__
    dave_fails = {"n": 0}
    dave_ok = {"n": 0}
    dave_skip = {"n": 0}

    def _keep_passthrough(session) -> None:  # type: ignore[no-untyped-def]
        try:
            # Allow unencrypted transition frames; long expiry only matters on disable.
            session.set_passthrough_mode(True, 600)
        except Exception:
            pass

    def _mapped_user_id(self) -> int | None:  # type: ignore[no-untyped-def]
        """Resolve the single speaker for this SSRC — never scan the whole MLS roster."""
        if self._cached_id is not None:
            try:
                return int(self._cached_id)
            except Exception:
                pass
        try:
            mapped = self.sink.voice_client._get_id_from_ssrc(self.ssrc)
            if mapped is not None:
                return int(mapped)
        except Exception:
            pass
        member = self._get_cached_member()
        if member is not None:
            try:
                return int(member.id)
            except Exception:
                pass
        return None

    def _init(self, router, ssrc):  # type: ignore[no-untyped-def]
        orig_init(self, router, ssrc)
        try:
            vc = self.sink.voice_client
            session = getattr(getattr(vc, "_connection", None), "dave_session", None)
            if session is not None:
                _keep_passthrough(session)
        except Exception:
            log.exception("DAVE set_passthrough_mode on decoder init failed")

    def _process_packet(self, packet):  # type: ignore[no-untyped-def]
        pcm = None
        member = self._get_cached_member()
        if member is None:
            try:
                self._cached_id = self.sink.voice_client._get_id_from_ssrc(self.ssrc)
            except Exception:
                self._cached_id = None
            member = self._get_cached_member()

        vc = self.sink.voice_client
        # Always re-read live connection state (Hermes #77998): dave_session can
        # be replaced on MLS reinit; never cache the object across packets.
        state = getattr(vc, "_connection", None)
        session = getattr(state, "dave_session", None) if state is not None else None
        protocol = int(getattr(state, "dave_protocol_version", 0) or 0) if state else 0
        silence = bool(getattr(packet, "is_silence", lambda: False)())
        payload = getattr(packet, "payload_type", getattr(packet, "type", 120))
        raw = getattr(packet, "decrypted_data", None)
        is_audio = packet and not silence and raw is not None and len(raw) > 3 and int(payload) == 120

        if session is not None and protocol > 0 and is_audio:
            _keep_passthrough(session)
            padding = bool(getattr(packet, "padding", False))
            cleaned = _strip_rtp_padding(bytes(raw), padding=padding)
            ready = bool(getattr(session, "ready", False))
            user_id = _mapped_user_id(self)

            if not ready or user_id is None:
                # MLS not ready / SSRC not mapped yet: passthrough strip only.
                # Never brute-force session.get_user_ids() — that poisons nonces.
                packet.decrypted_data = _strip_dave_passthrough_trailers(
                    cleaned, padding=False
                )
                dave_skip["n"] += 1
                if dave_skip["n"] in (1, 100, 500, 2000):
                    log.info(
                        "DAVE skip decrypt (#%s) ready=%s user=%s ssrc=%s — passthrough strip",
                        dave_skip["n"],
                        ready,
                        user_id,
                        self.ssrc,
                    )
            else:
                try:
                    packet.decrypted_data = session.decrypt(
                        user_id, MediaType.audio, cleaned
                    )
                    dave_ok["n"] += 1
                    _note_dave_result(True)
                    self._cached_id = user_id
                    member = self._get_cached_member()
                    if dave_ok["n"] == 1:
                        log.info(
                            "DAVE decrypt ok (first) user=%s ssrc=%s",
                            user_id,
                            self.ssrc,
                        )
                except Exception as exc:
                    exc_text = repr(exc)
                    if "UnencryptedWhenPassthroughDisabled" in exc_text:
                        # Valid transport Opus during epoch transition (pycord #3139).
                        packet.decrypted_data = _strip_dave_passthrough_trailers(
                            cleaned, padding=False
                        )
                        _note_dave_result(True)
                        dave_ok["n"] += 1
                    else:
                        dave_fails["n"] += 1
                        _note_dave_result(False)
                        if dave_fails["n"] in (1, 25, 50, 200, 1000):
                            log.warning(
                                "DAVE decrypt failed (#%s, user=%s, ready=%s): %r",
                                dave_fails["n"],
                                user_id,
                                ready,
                                exc,
                            )
                        # Never feed ciphertext to Opus — DTX silence keeps decoder alive.
                        packet.decrypted_data = opus_silence

        elif protocol > 0 and is_audio and session is None:
            # Protocol claims DAVE but session missing mid-reinit — don't Opus ciphertext.
            packet.decrypted_data = opus_silence

        if not self.sink.wants_opus():
            try:
                packet, pcm = self._decode_packet(packet)
            except OpusError:
                try:
                    self._decoder = OpusDecoder()
                except Exception:
                    self._decoder = None
                self._last_seq = packet.sequence
                self._last_ts = packet.timestamp
                return vr_opus.VoiceData(packet, member, pcm=b"")

        data = vr_opus.VoiceData(packet, member, pcm=pcm)
        self._last_seq = packet.sequence
        self._last_ts = packet.timestamp
        return data

    def _do_run(self) -> None:  # type: ignore[no-untyped-def]
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in list(self.waiter.items):
                    try:
                        data = decoder.pop_data()
                    except OpusError:
                        try:
                            decoder.reset()
                        except Exception:
                            pass
                        continue
                    except Exception:
                        log.exception("voice_recv decoder.pop_data failed")
                        try:
                            decoder.reset()
                        except Exception:
                            pass
                        continue
                    if data is not None:
                        try:
                            self.sink.write(data.source, data)
                        except Exception:
                            log.exception("voice_recv sink.write failed")

    vr_opus.PacketDecoder.__init__ = _init  # type: ignore[method-assign]
    vr_opus.PacketDecoder._process_packet = _process_packet  # type: ignore[method-assign]
    vr_router.PacketRouter._do_run = _do_run  # type: ignore[method-assign]
    _VOICE_RECV_PATCHED = True
    logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)
    log.info("Patched voice_recv for DAVE decrypt + OpusError survival")


async def _wait_for_dave(vc: discord.VoiceProtocol, *, timeout: float = 8.0) -> None:
    """MLS keys arrive after connect; wait briefly and enable passthrough."""
    # Log MLS binary opcodes so we can see whether Discord sends welcomes/commits.
    ws = getattr(vc, "ws", None)
    if ws is not None and not getattr(ws, "_erunda_mls_logged", False):
        orig_bin = ws.received_binary_message

        async def _logged_binary(msg: bytes) -> None:  # type: ignore[no-untyped-def]
            op = msg[2] if len(msg) > 2 else None
            await orig_bin(msg)
            state = getattr(vc, "_connection", None)
            session = getattr(state, "dave_session", None) if state is not None else None
            users = []
            try:
                users = list(session.get_user_ids()) if session is not None else []
            except Exception:
                pass
            log.info(
                "Voice MLS binary op=%s ready=%s mls_users=%s",
                op,
                getattr(session, "ready", None),
                users,
            )

        ws.received_binary_message = _logged_binary  # type: ignore[method-assign]
        ws._erunda_mls_logged = True  # type: ignore[attr-defined]

    deadline = datetime.now(timezone.utc).timestamp() + timeout
    last_users: list[int] = []
    while datetime.now(timezone.utc).timestamp() < deadline:
        state = getattr(vc, "_connection", None)
        session = getattr(state, "dave_session", None) if state is not None else None
        version = int(getattr(state, "dave_protocol_version", 0) or 0) if state else 0
        if session is None:
            await asyncio.sleep(0.15)
            continue
        try:
            session.set_passthrough_mode(True, 600)
        except Exception:
            pass
        ready = bool(getattr(session, "ready", False))
        try:
            users = [int(u) for u in session.get_user_ids()]
        except Exception:
            users = []
        if users != last_users or (ready and users):
            log.info(
                "DAVE state version=%s ready=%s mls_users=%s status=%s",
                version,
                ready,
                users,
                getattr(session, "status", None),
            )
            last_users = users
        if ready and users:
            return
        await asyncio.sleep(0.15)
    state = getattr(vc, "_connection", None)
    session = getattr(state, "dave_session", None) if state is not None else None
    log.warning(
        "DAVE still not ready after %.1fs: version=%s ready=%s users=%s status=%s",
        timeout,
        getattr(state, "dave_protocol_version", None),
        getattr(session, "ready", None),
        list(session.get_user_ids()) if session is not None else None,
        getattr(session, "status", None),
    )


class _UserBuffer:
    __slots__ = ("pcm", "last_packet_at", "flushing", "flush_started_at")

    def __init__(self) -> None:
        self.pcm = bytearray()
        self.last_packet_at = 0.0
        self.flushing = False
        self.flush_started_at = 0.0


class VoiceListenService:
    def __init__(self, db: Database, ai: AIService) -> None:
        self.db = db
        self.ai = ai
        self.dossier_service = None
        self._clients: dict[int, discord.VoiceProtocol] = {}
        self._buffers: dict[int, dict[int, _UserBuffer]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._dossier_dates: dict[int, date] = {}
        self._bot: ErundaBot | None = None
        self._credits_shutdown = False
        # (guild_id, sound_name) → resolved soundboard entry
        self._sound_cache: dict[tuple[int, str], discord.SoundboardSound | None] = {}
        self._sound_cooldown_until: dict[int, float] = {}
        self._tts_play_lock = asyncio.Lock()
        self._tts_cache: dict[tuple[str, str], bytes] = {}
        self._voice_muted: dict[int, set[int]] = defaultdict(set)
        self._aisa_greeting_cooldown_until: dict[int, float] = {}
        self._announce_channels: dict[int, int] = {}
        self._pcm_packets: dict[int, int] = defaultdict(int)
        self._unknown_speaker_packets: dict[int, int] = defaultdict(int)
        self._unknown_streak: dict[int, int] = defaultdict(int)
        self._last_pcm_at: dict[int, float] = {}
        self._last_callback_at: dict[int, float] = {}
        self._last_useful_stt_at: dict[int, float] = {}
        self._last_heal_at: dict[int, float] = {}
        self._last_hard_reconnect_at: dict[int, float] = {}
        self._recover_grace_until: dict[int, float] = {}
        self._session_started_at: dict[int, float] = {}
        self._last_soft_rebind_at: dict[int, float] = {}
        self._soft_rebind_times: dict[int, list[float]] = defaultdict(list)
        self._sink_dead_streak: dict[int, int] = defaultdict(int)
        self._hallucination_streak: dict[int, int] = defaultdict(int)
        self._healing: set[int] = set()
        # Guilds where we initiated disconnect (leave/join/resume) — ignore voice_state leave.
        self._expecting_disconnect: set[int] = set()
        self._health_tick: float = 0.0
        self._health_status_tick: float = 0.0
        self._empty_pcm_streak: dict[int, int] = defaultdict(int)
        # guild_id -> ssrc -> user_id (survives brief speaking-map gaps)
        self._ssrc_users: dict[int, dict[int, int]] = defaultdict(dict)
        # (guild_id, user_id) -> recent STT chunks for cross-pause sixseven
        self._recent_stt: dict[tuple[int, int], list[tuple[float, str]]] = defaultdict(list)

    def is_listening(self, guild_id: int) -> bool:
        vc = self._clients.get(guild_id)
        return vc is not None and vc.is_connected()

    def listening_channel(self, guild_id: int) -> discord.abc.Connectable | None:
        vc = self._clients.get(guild_id)
        if vc is None or not vc.is_connected():
            return None
        return vc.channel

    def _ensure_watchdog(self, bot: ErundaBot) -> None:
        self._bot = bot
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._silence_watchdog(bot))

    async def start(self, bot: ErundaBot) -> None:
        self._ensure_watchdog(bot)
        await self.reload_voice_muted_cache()
        asyncio.create_task(self._restore_sessions(bot))

    async def reload_voice_muted_cache(self) -> None:
        try:
            rows = await self.db.list_all_voice_listen_muted()
        except Exception:
            log.exception("Failed to load voice_listen_muted cache")
            return
        muted: dict[int, set[int]] = defaultdict(set)
        for guild_id, user_id in rows:
            muted[int(guild_id)].add(int(user_id))
        self._voice_muted = muted

    def set_user_muted(self, guild_id: int, user_id: int, muted: bool) -> None:
        bucket = self._voice_muted.setdefault(guild_id, set())
        if muted:
            bucket.add(user_id)
        else:
            bucket.discard(user_id)

    async def stop_all(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
        # Keep DB resume targets so redeploy can rejoin.
        for guild_id in list(self._clients):
            await self.leave(guild_id, clear_resume=False)

    async def _restore_sessions(self, bot: ErundaBot) -> None:
        await bot.wait_until_ready()
        await asyncio.sleep(2.0)
        try:
            guilds = await self.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for voice listen restore")
            return
        for config in guilds:
            channel_id = config.voice_listen_channel_id
            if channel_id is None:
                continue
            if not voice_listen_allowed(config.guild_id):
                log.info(
                    "Skip voice restore guild=%s — not in VOICE_LISTEN_GUILD_IDS",
                    config.guild_id,
                )
                try:
                    await self.db.update_guild(
                        config.guild_id,
                        voice_listen_channel_id=None,
                    )
                except Exception:
                    log.exception(
                        "Failed to clear voice_listen_channel_id for guild %s",
                        config.guild_id,
                    )
                continue
            if self.is_listening(config.guild_id):
                continue
            guild = bot.get_guild(config.guild_id)
            if guild is None:
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                log.warning(
                    "Voice restore skipped: channel %s missing in guild %s",
                    channel_id,
                    config.guild_id,
                )
                continue
            try:
                text = await self.join(
                    bot,
                    channel,
                    announce_channel_id=config.voice_announce_channel_id,
                )
                log.info(
                    "Restored voice listen guild=%s channel=%s (%s)",
                    config.guild_id,
                    channel_id,
                    text.split("\n", 1)[0],
                )
            except Exception:
                log.exception(
                    "Failed to restore voice listen guild=%s channel=%s",
                    config.guild_id,
                    channel_id,
                )

    async def join(
        self,
        bot: ErundaBot,
        channel: discord.VoiceChannel | discord.StageChannel,
        *,
        announce_channel_id: int | None = None,
    ) -> str:
        if not voice_listen_allowed(channel.guild.id):
            raise ValueError(
                "Войс → текст на этом сервере выключен "
                "(только Ерундульки и разрешённый второй сервер)"
            )
        if self.ai.stt_credits_exhausted:
            raise ValueError(CREDITS_EXHAUSTED_MESSAGE)
        if not self.ai.transcription_enabled:
            raise ValueError(
                "Нужен OPENROUTER_API_KEY в .env.production для распознавания речи"
            )
        try:
            from discord.ext import voice_recv
        except ImportError as exc:
            raise ValueError(
                "Пакет discord-ext-voice-recv не установлен на сервере"
            ) from exc

        _patch_voice_recv()

        guild = channel.guild
        async with self._lock:
            existing = guild.voice_client
            if existing is not None and existing.is_connected():
                if existing.channel and existing.channel.id == channel.id:
                    if guild.id not in self._clients:
                        self._clients[guild.id] = existing
                    if announce_channel_id is not None:
                        self._announce_channels[guild.id] = int(announce_channel_id)
                    await self.db.update_guild(
                        guild.id,
                        voice_listen_channel_id=int(channel.id),
                        voice_announce_channel_id=self._announce_channels.get(guild.id),
                    )
                    # Sink can die while vc stays connected — always rebind.
                    self._bind_listen_sink(bot, guild, existing)
                    self._ensure_watchdog(bot)
                    tip = await self._soundboard_status(guild, channel)
                    return f"Уже слушаю {channel.mention}\n{tip}"
                self._expecting_disconnect.add(guild.id)
                try:
                    await existing.disconnect(force=True)
                except Exception:
                    self._expecting_disconnect.discard(guild.id)
                    raise

            if not discord.opus.is_loaded():
                raise ValueError(
                    "libopus не загружен — распознавание речи и саундборд недоступны"
                )

            vc = await channel.connect(
                cls=voice_recv.VoiceRecvClient,
                self_deaf=False,
                self_mute=False,
            )
            self._clients[guild.id] = vc
            self._buffers[guild.id] = {}
            self._reset_voice_counters(guild.id)
            if announce_channel_id is not None:
                self._announce_channels[guild.id] = int(announce_channel_id)
                log.info(
                    "Voice announce channel for guild %s → %s",
                    guild.id,
                    announce_channel_id,
                )
            # Grace/session clock BEFORE bind so early RTP counts as real traffic.
            self._mark_voice_join_grace(guild.id)
            # Start listener first so Discord begins RTP, then wait for MLS keys.
            self._bind_listen_sink(bot, guild, vc)
            await _wait_for_dave(vc, timeout=10.0)
            await asyncio.sleep(0.8)
            # Rebind after DAVE ready — first bind can miss the key window.
            self._bind_listen_sink(bot, guild, vc)
            self._ensure_watchdog(bot)
            await self.db.update_guild(
                guild.id,
                voice_listen_channel_id=int(channel.id),
                voice_announce_channel_id=self._announce_channels.get(guild.id),
            )
            tip = await self._soundboard_status(guild, channel)
            where = ""
            if guild.id in self._announce_channels:
                where = f"\nРеплики пишу в <#{self._announce_channels[guild.id]}>"
            return f"Слушаю {channel.mention}\n{tip}{where}"

    def _reset_voice_counters(self, guild_id: int) -> None:
        self._pcm_packets[guild_id] = 0
        self._unknown_speaker_packets[guild_id] = 0
        self._unknown_streak[guild_id] = 0
        self._empty_pcm_streak[guild_id] = 0
        self._last_pcm_at.pop(guild_id, None)
        self._last_callback_at.pop(guild_id, None)

    def _mark_voice_join_grace(self, guild_id: int) -> None:
        """Start a session clock; do NOT fake last_pcm (that caused recover loops)."""
        now = datetime.now(timezone.utc).timestamp()
        self._session_started_at[guild_id] = now
        self._recover_grace_until[guild_id] = now + VOICE_RECOVER_GRACE_SECONDS
        self._last_pcm_at.pop(guild_id, None)
        self._last_callback_at.pop(guild_id, None)

    def _mark_heal_grace(self, guild_id: int) -> None:
        """Pause health checks without wiping RTP timestamps / session clock."""
        now = datetime.now(timezone.utc).timestamp()
        self._recover_grace_until[guild_id] = now + 20.0

    @staticmethod
    def _voice_ws_alive(vc: discord.VoiceProtocol) -> bool:
        if not getattr(vc, "is_connected", lambda: False)():
            return False
        ws = getattr(vc, "ws", None)
        if ws is None:
            return False
        if getattr(ws, "closed", False):
            return False
        raw = getattr(ws, "ws", None) or getattr(ws, "_ws", None)
        if raw is not None and getattr(raw, "closed", False):
            return False
        return True

    async def _wait_for_rtp(self, guild_id: int, *, timeout: float = VOICE_RTP_WAIT_SECONDS) -> bool:
        """Return True once a real voice callback arrives after session start."""
        started = self._session_started_at.get(guild_id, 0.0)
        deadline = datetime.now(timezone.utc).timestamp() + timeout
        while datetime.now(timezone.utc).timestamp() < deadline:
            cb = self._last_callback_at.get(guild_id, 0.0)
            if cb >= started and started > 0:
                log.info(
                    "Voice RTP confirmed guild=%s after %.1fs",
                    guild_id,
                    cb - started,
                )
                return True
            await asyncio.sleep(0.4)
        log.warning("No voice RTP within %.0fs after join guild=%s", timeout, guild_id)
        return False

    def _bind_listen_sink(
        self,
        bot: ErundaBot,
        guild: discord.Guild,
        vc: discord.VoiceProtocol,
        *,
        restart: bool = True,
    ) -> None:
        from discord.ext import voice_recv

        try:
            listening = bool(getattr(vc, "is_listening", lambda: False)())
        except Exception:
            listening = False
        if listening and not restart:
            # Mid-call stop/start desyncs DAVE nonce windows — leave sink alone.
            return
        if listening and restart:
            try:
                vc.stop_listening()
            except Exception:
                pass

        def on_voice(user: discord.User | discord.Member | None, data) -> None:
            now = datetime.now(timezone.utc).timestamp()
            self._last_callback_at[guild.id] = now
            user_id = self._resolve_speaker_id(guild.id, vc, user, data)
            if user_id is None:
                self._unknown_speaker_packets[guild.id] += 1
                self._unknown_streak[guild.id] += 1
                streak = self._unknown_streak[guild.id]
                total = self._unknown_speaker_packets[guild.id]
                if streak in (1, 40, 80, 160, 400) or total in (200, 1000):
                    log.warning(
                        "Voice packets without speaker map in guild %s (streak=%s total=%s)",
                        guild.id,
                        streak,
                        total,
                    )
                # Never soft-rebind/heal mid-call for unknown SSRC — remount desyncs DAVE.
                if streak in (VOICE_RECOVER_UNKNOWN_STREAK, VOICE_HARD_RECOVER_UNKNOWN_STREAK):
                    log.warning(
                        "Unknown speaker streak=%s guild=%s — leave sink alone (DAVE-safe)",
                        streak,
                        guild.id,
                    )
                return
            # Decay instead of hard reset — flapping known/unknown must still escalate.
            self._unknown_streak[guild.id] = max(0, self._unknown_streak[guild.id] - 8)
            if user_id in self._voice_muted.get(guild.id, set()):
                return
            pcm = getattr(data, "pcm", None)
            if not pcm:
                # DAVE/Opus failure returns empty VoiceData while RTP still flows.
                streak = self._empty_pcm_streak[guild.id] + 1
                self._empty_pcm_streak[guild.id] = streak
                if streak in (1, 40, 100, 400, 1000):
                    log.warning(
                        "Empty PCM streak=%s guild=%s user=%s (DAVE/Opus likely broken)",
                        streak,
                        guild.id,
                        user_id,
                    )
                return
            self._empty_pcm_streak[guild.id] = 0
            pcm_bytes = bytes(pcm)
            rms = pcm_rms(pcm_bytes)
            # DAVE garbage still yields PCM; only loud frames count as healthy audio.
            if rms >= MIN_PCM_RMS:
                self._pcm_packets[guild.id] += 1
                self._last_pcm_at[guild.id] = now
                if self._pcm_packets[guild.id] == 1:
                    log.info("First voice PCM packet in guild %s user=%s", guild.id, user_id)
            bot.loop.call_soon_threadsafe(
                self._append_pcm,
                guild.id,
                user_id,
                pcm_bytes,
            )

        vc.listen(voice_recv.BasicSink(on_voice, decode=True))

    def _schedule_soft_rebind(self, guild_id: int, *, reason: str) -> None:
        """Only touch sink when RTP/callbacks are actually dead.

        Mid-call listen()/stop_listening() desyncs DAVE (NoValidCryptorFound).
        False `is_listening()==False` while packets still flow must be ignored.
        """
        bot = self._bot
        if bot is None or guild_id in self._healing:
            return
        now = datetime.now(timezone.utc).timestamp()
        last_cb = self._last_callback_at.get(guild_id, 0.0)
        last_pcm = self._last_pcm_at.get(guild_id, 0.0)
        cb_age = (now - last_cb) if last_cb > 0 else 1e9
        pcm_age = (now - last_pcm) if last_pcm > 0 else 1e9
        # Live audio path — do not remount sink.
        if cb_age < 20.0 or pcm_age < 20.0:
            log.info(
                "Skip soft rebind guild=%s (%s) — live audio cb_age=%.1fs pcm_age=%.1fs",
                guild_id,
                reason,
                cb_age,
                pcm_age,
            )
            return
        if now - self._last_soft_rebind_at.get(guild_id, 0.0) < VOICE_SOFT_REBIND_COOLDOWN_SECONDS:
            return
        self._last_soft_rebind_at[guild_id] = now
        log.warning("Scheduling voice soft rebind guild=%s (%s)", guild_id, reason)

        def _go() -> None:
            asyncio.create_task(self._soft_rebind_listen(guild_id, reason=reason))

        bot.loop.call_soon_threadsafe(_go)

    async def _soft_rebind_listen(self, guild_id: int, *, reason: str) -> None:
        bot = self._bot
        if bot is None or guild_id in self._healing:
            return
        guild = bot.get_guild(guild_id)
        vc = self._clients.get(guild_id)
        if guild is None or vc is None or not vc.is_connected():
            self._schedule_resume_if_disconnected(
                guild_id, reason=f"soft_rebind_no_vc:{reason}"
            )
            return
        now = datetime.now(timezone.utc).timestamp()
        last_cb = self._last_callback_at.get(guild_id, 0.0)
        cb_age = (now - last_cb) if last_cb > 0 else 1e9
        if cb_age < 20.0:
            log.info(
                "Abort soft rebind guild=%s (%s) — callbacks resumed age=%.1fs",
                guild_id,
                reason,
                cb_age,
            )
            return
        try:
            listening = bool(getattr(vc, "is_listening", lambda: False)())
        except Exception:
            listening = False
        if listening:
            log.info(
                "Abort soft rebind guild=%s (%s) — sink reports listening again",
                guild_id,
                reason,
            )
            return
        log.warning("Soft-rebind voice sink guild=%s (%s)", guild_id, reason)
        self._unknown_streak[guild_id] = 0
        try:
            state = getattr(vc, "_connection", None)
            session = getattr(state, "dave_session", None) if state is not None else None
            if session is not None:
                session.set_passthrough_mode(True, 600)
        except Exception:
            pass
        # Never stop_listening / restart=True mid-call — only start if truly dead.
        self._bind_listen_sink(bot, guild, vc, restart=False)
        self._mark_heal_grace(guild_id)

    def _schedule_heal(self, guild_id: int, *, reason: str) -> None:
        """In-place fix: reinit MLS/DAVE + rebind sink. Does not leave the channel."""
        bot = self._bot
        if bot is None:
            return
        now = datetime.now(timezone.utc).timestamp()
        if guild_id in self._healing:
            return
        last = self._last_heal_at.get(guild_id, 0.0)
        if now - last < VOICE_HEAL_COOLDOWN_SECONDS:
            return
        log.warning("Scheduling voice heal (no leave) guild=%s (%s)", guild_id, reason)

        def _go() -> None:
            asyncio.create_task(self._heal_listen(guild_id, reason=reason))

        bot.loop.call_soon_threadsafe(_go)

    async def _heal_listen(self, guild_id: int, *, reason: str) -> None:
        """In-place: passthrough + sink rebind. Never call reinit_dave_session here —
        it nukes MLS keys and often fails with 'closing transport', then loops forever.
        """
        if guild_id in self._healing:
            return
        bot = self._bot
        if bot is None or self.ai.stt_credits_exhausted:
            return
        now = datetime.now(timezone.utc).timestamp()
        last = self._last_heal_at.get(guild_id, 0.0)
        if now - last < VOICE_HEAL_COOLDOWN_SECONDS:
            return
        self._healing.add(guild_id)
        self._last_heal_at[guild_id] = now
        try:
            guild = bot.get_guild(guild_id)
            vc = self._clients.get(guild_id)
            if guild is None or vc is None or not vc.is_connected():
                self._schedule_resume_if_disconnected(
                    guild_id, reason=f"heal_no_vc:{reason}"
                )
                return
            log.warning(
                "Healing voice listen in place guild=%s (%s)",
                guild_id,
                reason,
            )
            state = getattr(vc, "_connection", None)
            session = getattr(state, "dave_session", None) if state is not None else None
            if session is not None:
                try:
                    session.set_passthrough_mode(True, 600)
                except Exception:
                    log.exception("DAVE passthrough during heal failed guild=%s", guild_id)
            self._unknown_streak[guild_id] = 0
            self._hallucination_streak[guild_id] = 0
            # Rebind only if sink died — stop/start mid-call desyncs DAVE nonce windows.
            try:
                listening = bool(getattr(vc, "is_listening", lambda: False)())
            except Exception:
                listening = False
            if not listening:
                self._bind_listen_sink(bot, guild, vc, restart=False)
                _reset_dave_stats()
            self._mark_heal_grace(guild_id)
            log.info(
                "Voice listen healed in place guild=%s listening=%s",
                guild_id,
                listening,
            )
        except Exception:
            log.exception("Voice listen heal failed guild=%s", guild_id)
        finally:
            self._healing.discard(guild_id)

    def _schedule_resume_if_disconnected(self, guild_id: int, *, reason: str) -> None:
        """Join again only if Discord already dropped the bot. Never leave while connected."""
        bot = self._bot
        if bot is None:
            return
        if self.ai.stt_credits_exhausted:
            return
        vc = self._clients.get(guild_id)
        if vc is not None and vc.is_connected():
            log.info(
                "Skip resume join guild=%s — still connected (%s); heal instead",
                guild_id,
                reason,
            )
            self._schedule_heal(guild_id, reason=f"skip_resume:{reason}")
            return
        # discord.py may already be reconnecting — don't force a second connect.
        guild = bot.get_guild(guild_id)
        guild_vc = guild.voice_client if guild is not None else None
        if guild_vc is not None and guild_vc.is_connected():
            self._clients[guild_id] = guild_vc
            log.info(
                "Skip resume join guild=%s — guild.voice_client already up (%s)",
                guild_id,
                reason,
            )
            return
        now = datetime.now(timezone.utc).timestamp()
        if guild_id in self._healing:
            return
        last = self._last_hard_reconnect_at.get(guild_id, 0.0)
        if now - last < VOICE_RESUME_JOIN_COOLDOWN_SECONDS:
            return
        # Stamp cooldown immediately so health spam cannot queue parallel joins.
        self._last_hard_reconnect_at[guild_id] = now
        log.warning(
            "Scheduling voice resume join guild=%s (%s)",
            guild_id,
            reason,
        )

        def _go() -> None:
            asyncio.create_task(self._resume_join_if_disconnected(guild_id, reason=reason))

        bot.loop.call_soon_threadsafe(_go)

    def _schedule_zombie_rejoin(self, guild_id: int, *, reason: str) -> None:
        """Force leave+join while keeping DB resume — for connected-but-deaf sessions."""
        bot = self._bot
        if bot is None or self.ai.stt_credits_exhausted:
            return
        if guild_id in self._healing:
            return
        now = datetime.now(timezone.utc).timestamp()
        last = self._last_hard_reconnect_at.get(guild_id, 0.0)
        if now - last < VOICE_RESUME_JOIN_COOLDOWN_SECONDS:
            return
        self._last_hard_reconnect_at[guild_id] = now
        log.warning("Scheduling zombie rejoin guild=%s (%s)", guild_id, reason)

        def _go() -> None:
            asyncio.create_task(self._zombie_rejoin(guild_id, reason=reason))

        bot.loop.call_soon_threadsafe(_go)

    async def _zombie_rejoin(self, guild_id: int, *, reason: str) -> None:
        bot = self._bot
        if bot is None or self.ai.stt_credits_exhausted:
            return
        if guild_id in self._healing:
            return
        self._healing.add(guild_id)
        self._last_hard_reconnect_at[guild_id] = datetime.now(timezone.utc).timestamp()
        try:
            config = await self.db.get_guild(guild_id)
            channel_id = config.voice_listen_channel_id if config else None
            announce_id = self._announce_channels.get(guild_id)
            if announce_id is None and config is not None:
                announce_id = config.voice_announce_channel_id
            if not channel_id:
                log.warning("Zombie rejoin aborted: no resume channel guild=%s", guild_id)
                return
            # Drop dead/zombie client but keep DB resume target.
            await self.leave(guild_id, flush=False, clear_resume=False)
            # leave() cleared in-memory announce — restore for join
            if announce_id is not None:
                self._announce_channels[guild_id] = int(announce_id)
            # Re-assert resume id (leave with clear_resume=False keeps it, but be safe).
            await self.db.update_guild(
                guild_id,
                voice_listen_channel_id=int(channel_id),
                voice_announce_channel_id=announce_id,
            )
            guild = bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild else None
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                log.warning("Zombie rejoin aborted: channel missing guild=%s", guild_id)
                return
            log.warning(
                "Zombie rejoin guild=%s channel=%s (%s)",
                guild_id,
                channel.id,
                reason,
            )
            _reset_dave_stats()
            await self.join(bot, channel, announce_channel_id=announce_id)
            _reset_dave_stats()
            ok = await self._wait_for_rtp(guild_id, timeout=VOICE_RTP_WAIT_SECONDS)
            if ok:
                log.info("Zombie rejoin got RTP guild=%s", guild_id)
            else:
                log.error("Zombie rejoin still no RTP guild=%s", guild_id)
        except Exception:
            log.exception("Zombie rejoin failed guild=%s", guild_id)
        finally:
            self._healing.discard(guild_id)

    async def _resume_join_if_disconnected(self, guild_id: int, *, reason: str) -> None:
        bot = self._bot
        if bot is None or self.ai.stt_credits_exhausted:
            return
        vc = self._clients.get(guild_id)
        if vc is not None and vc.is_connected():
            log.info("Resume join skipped guild=%s — already connected (%s)", guild_id, reason)
            return
        # Cooldown is stamped in _schedule_resume_if_disconnected so health spam
        # cannot queue parallel joins. Do NOT re-check it here — that made every
        # scheduled resume return immediately without joining (0 joins / hundreds
        # of "Scheduling" logs).
        self._healing.add(guild_id)
        self._last_hard_reconnect_at[guild_id] = datetime.now(timezone.utc).timestamp()
        try:
            # Drop zombie VoiceClient after failed Discord reconnect so join()
            # does not trip over a half-dead connection object.
            dead = self._clients.pop(guild_id, None)
            if dead is not None:
                self._expecting_disconnect.add(guild_id)
                try:
                    await dead.disconnect(force=True)
                except Exception:
                    self._expecting_disconnect.discard(guild_id)
                    log.exception("Failed to drop dead voice client guild=%s", guild_id)
            guild = bot.get_guild(guild_id)
            if guild is not None and guild.voice_client is not None:
                self._expecting_disconnect.add(guild_id)
                try:
                    await guild.voice_client.disconnect(force=True)
                except Exception:
                    self._expecting_disconnect.discard(guild_id)
                    log.exception("Failed to drop guild.voice_client guild=%s", guild_id)

            announce_id = self._announce_channels.get(guild_id)
            config = await self.db.get_guild(guild_id)
            if announce_id is None and config is not None:
                announce_id = config.voice_announce_channel_id
            channel_id = config.voice_listen_channel_id if config else None
            # Kick via Discord UI clears resume — abort if already cleared.
            if not channel_id:
                log.info("Resume join aborted: listen cleared guild=%s (%s)", guild_id, reason)
                return
            channel = guild.get_channel(channel_id) if guild and channel_id else None
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                log.warning("Resume join aborted: no channel guild=%s", guild_id)
                return
            # Re-check after awaits — external kick may have cleared resume mid-flight.
            config = await self.db.get_guild(guild_id)
            if config is None or not config.voice_listen_channel_id:
                log.info("Resume join aborted: listen cleared mid-flight guild=%s", guild_id)
                return
            log.warning(
                "Resume join after Discord drop guild=%s channel=%s (%s)",
                guild_id,
                channel.id,
                reason,
            )
            _reset_dave_stats()
            await self.join(bot, channel, announce_channel_id=announce_id)
            _reset_dave_stats()
            ok = await self._wait_for_rtp(guild_id, timeout=VOICE_RTP_WAIT_SECONDS)
            if ok:
                log.info("Resume join got RTP guild=%s", guild_id)
            else:
                log.error("Resume join still no RTP guild=%s", guild_id)
        except Exception:
            log.exception("Voice resume join failed guild=%s", guild_id)
        finally:
            self._healing.discard(guild_id)

    async def leave(self, guild_id: int, *, flush: bool = True, clear_resume: bool = True) -> str:
        async with self._lock:
            vc = self._clients.pop(guild_id, None)
            buffers = self._buffers.pop(guild_id, {})
        bot = self._bot
        if (
            flush
            and bot is not None
            and not self.ai.stt_credits_exhausted
        ):
            for user_id, buf in list(buffers.items()):
                if len(buf.pcm) < int(MIN_AUDIO_SECONDS * BYTES_PER_SECOND):
                    continue
                if buf.flushing:
                    continue
                buf.flushing = True
                pcm = bytes(buf.pcm)
                buf.pcm.clear()
                await self._transcribe_and_store(bot, guild_id, user_id, pcm, buf)
        self._announce_channels.pop(guild_id, None)
        self._pcm_packets.pop(guild_id, None)
        self._unknown_speaker_packets.pop(guild_id, None)
        self._unknown_streak.pop(guild_id, None)
        self._last_pcm_at.pop(guild_id, None)
        self._last_callback_at.pop(guild_id, None)
        self._last_useful_stt_at.pop(guild_id, None)
        self._session_started_at.pop(guild_id, None)
        self._recover_grace_until.pop(guild_id, None)
        self._soft_rebind_times.pop(guild_id, None)
        self._hallucination_streak.pop(guild_id, None)
        self._empty_pcm_streak.pop(guild_id, None)
        self._ssrc_users.pop(guild_id, None)
        for key in [k for k in self._recent_stt if k[0] == guild_id]:
            self._recent_stt.pop(key, None)
        if clear_resume:
            try:
                await self.db.update_guild(
                    guild_id,
                    voice_listen_channel_id=None,
                    voice_announce_channel_id=None,
                )
            except Exception:
                log.exception("Failed to clear voice listen resume for guild %s", guild_id)
        if vc is not None and vc.is_connected():
            try:
                vc.stop_listening()
            except Exception:
                pass
            self._expecting_disconnect.add(guild_id)
            try:
                await vc.disconnect(force=True)
            except Exception:
                self._expecting_disconnect.discard(guild_id)
                raise
            return "Вышел из войса"
        return "Бот не был в войсе"

    async def handle_bot_voice_disconnect(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Voice channel left in voice_state — keep resume and rejoin.

        Critical: Discord voice WS 1006 makes discord.py reconnect and often
        emits after.channel=None mid-handshake. Clearing resume here permanently
        killed listening while the library was still reconnecting.
        Real stop = /z-admin voice_leave only.
        """
        bot = self._bot
        if bot is None or bot.user is None:
            return
        if member.id != bot.user.id:
            return
        if before.channel is None or after.channel is not None:
            return
        guild_id = member.guild.id
        if guild_id in self._expecting_disconnect:
            self._expecting_disconnect.discard(guild_id)
            return
        had_client = guild_id in self._clients
        config = await self.db.get_guild(guild_id)
        had_resume = bool(config and config.voice_listen_channel_id)
        if not had_client and not had_resume:
            return
        # Persist channel if somehow missing so health/resume can find it.
        if before.channel is not None and not had_resume:
            try:
                await self.db.update_guild(
                    guild_id,
                    voice_listen_channel_id=int(before.channel.id),
                    voice_announce_channel_id=self._announce_channels.get(guild_id),
                )
            except Exception:
                log.exception("Failed to persist voice resume after drop guild=%s", guild_id)
        log.warning(
            "Bot voice_state left channel guild=%s channel=%s — keep resume, schedule rejoin",
            guild_id,
            before.channel.id,
        )
        self._schedule_resume_if_disconnected(guild_id, reason="voice_state_left")

    def _sixseven_from_context(
        self,
        guild_id: int,
        user_id: int,
        text: str,
    ) -> tuple[bool, str]:
        """Match meme in this chunk or across a short pause (сикс + севен)."""
        now = datetime.now(timezone.utc).timestamp()
        key = (guild_id, user_id)
        chunks = self._recent_stt[key]
        cleaned = (text or "").strip()
        if cleaned:
            chunks.append((now, cleaned))
        cutoff = now - SIXSEVEN_CONTEXT_SECONDS
        chunks[:] = [(ts, part) for ts, part in chunks if ts >= cutoff][
            -SIXSEVEN_CONTEXT_MAX_CHUNKS:
        ]
        if mentions_sixseven(cleaned):
            return True, cleaned
        combined = " ".join(part for _, part in chunks)
        if len(chunks) >= 2 and mentions_sixseven(combined):
            return True, combined
        return False, cleaned

    def _resolve_speaker_id(
        self,
        guild_id: int,
        vc: discord.VoiceProtocol,
        user: discord.User | discord.Member | None,
        data: object,
    ) -> int | None:
        packet = getattr(data, "packet", None)
        ssrc = getattr(packet, "ssrc", None)
        if user is not None:
            if getattr(user, "bot", False):
                return None
            user_id = int(user.id)
            if ssrc is not None:
                self._ssrc_users[guild_id][int(ssrc)] = user_id
            return user_id
        if ssrc is not None:
            cached = self._ssrc_users[guild_id].get(int(ssrc))
            if cached is not None:
                return cached
            getter = getattr(vc, "_get_id_from_ssrc", None)
            if callable(getter):
                try:
                    user_id = getter(ssrc)
                except Exception:
                    user_id = None
                if user_id is not None:
                    self._ssrc_users[guild_id][int(ssrc)] = int(user_id)
                    return int(user_id)
        # Speaking-map sometimes lags; prefer unmuted / actively speaking humans.
        channel = getattr(vc, "channel", None)
        members = getattr(channel, "members", None) or []
        humans = [m for m in members if not getattr(m, "bot", False)]
        if len(humans) == 1:
            user_id = int(humans[0].id)
            if ssrc is not None:
                self._ssrc_users[guild_id][int(ssrc)] = user_id
            return user_id
        live: list[discord.Member] = []
        for member in humans:
            state = getattr(member, "voice", None)
            if state is None:
                continue
            if getattr(state, "self_mute", False) or getattr(state, "mute", False):
                continue
            live.append(member)
        if len(live) == 1:
            user_id = int(live[0].id)
            if ssrc is not None:
                self._ssrc_users[guild_id][int(ssrc)] = user_id
            return user_id
        get_speaking = getattr(vc, "get_speaking", None)
        if callable(get_speaking) and live:
            speaking_members = []
            for member in live:
                try:
                    if get_speaking(member):
                        speaking_members.append(member)
                except Exception:
                    continue
            if len(speaking_members) == 1:
                user_id = int(speaking_members[0].id)
                if ssrc is not None:
                    self._ssrc_users[guild_id][int(ssrc)] = user_id
                return user_id
        return None

    async def leave_all_credits_exhausted(self) -> None:
        """Stop listening everywhere after OpenRouter 402."""
        if self._credits_shutdown:
            return
        self._credits_shutdown = True
        self.ai.mark_stt_credits_exhausted()
        for guild_id in list(self._clients):
            try:
                # Keep DB resume target so bot rejoins after credits are restored.
                await self.leave(guild_id, flush=False, clear_resume=False)
            except Exception:
                log.exception("Failed to leave voice after credits exhausted (%s)", guild_id)

    def _append_pcm(self, guild_id: int, user_id: int, pcm: bytes) -> None:
        bucket = self._buffers.setdefault(guild_id, {})
        buf = bucket.get(user_id)
        if buf is None:
            buf = _UserBuffer()
            bucket[user_id] = buf
        buf.pcm.extend(pcm)
        buf.last_packet_at = datetime.now(timezone.utc).timestamp()
        max_bytes = int(MAX_BUFFER_SECONDS * BYTES_PER_SECOND)
        if len(buf.pcm) >= max_bytes and not buf.flushing:
            # Only poke the watchdog — NEVER set flushing here. Setting flushing
            # without spawning STT deadlocks the user forever (watchdog skips
            # flushing buffers, and nothing else clears the flag).
            buf.last_packet_at = 0.0

    async def _silence_watchdog(self, bot: ErundaBot) -> None:
        await bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(0.1)
                now = datetime.now(timezone.utc).timestamp()
                for guild_id, users in list(self._buffers.items()):
                    for user_id, buf in list(users.items()):
                        if buf.flushing:
                            started = buf.flush_started_at or now
                            if now - started >= VOICE_FLUSH_STUCK_SECONDS:
                                log.warning(
                                    "STT flush stuck user=%s guild=%s for %.0fs — force unlock",
                                    user_id,
                                    guild_id,
                                    now - started,
                                )
                                buf.flushing = False
                                buf.flush_started_at = 0.0
                            else:
                                continue
                        if not buf.pcm:
                            continue
                        silent_for = now - buf.last_packet_at
                        max_bytes = int(MAX_BUFFER_SECONDS * BYTES_PER_SECOND)
                        if silent_for < SILENCE_FLUSH_MEME_SECONDS and len(buf.pcm) < max_bytes:
                            continue
                        if silent_for < SILENCE_FLUSH_SECONDS and len(buf.pcm) < max_bytes:
                            seconds = len(buf.pcm) / BYTES_PER_SECOND
                            if not (
                                seconds <= MEME_FLUSH_MAX_SECONDS
                                and pcm_rms(buf.pcm) >= MEME_FLUSH_MIN_RMS
                            ):
                                continue
                        if len(buf.pcm) < int(MIN_AUDIO_SECONDS * BYTES_PER_SECOND):
                            buf.pcm.clear()
                            continue
                        buf.flushing = True
                        buf.flush_started_at = now
                        pcm = bytes(buf.pcm)
                        buf.pcm.clear()
                        asyncio.create_task(
                            self._transcribe_and_store(bot, guild_id, user_id, pcm, buf)
                        )
                if now - self._health_tick >= VOICE_HEALTH_CHECK_EVERY:
                    self._health_tick = now
                    await self._check_voice_health(bot, now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Voice silence watchdog failed")

    async def _check_voice_health(self, bot: ErundaBot, now: float) -> None:
        """Health monitor: resume only if Discord already dropped the bot.

        Never leave+join / soft-remount while connected — that desyncs DAVE.
        Decrypt correctness (SSRC-mapped user only) is what keeps hearing alive.

        Important: do NOT gate on is_listening() before the disconnect check —
        is_listening requires is_connected(), which made resume unreachable.
        """
        status_due = now - self._health_status_tick >= VOICE_HEALTH_STATUS_EVERY
        if status_due:
            self._health_status_tick = now

        tracked = set(self._clients)
        for guild_id in list(self._clients):
            if guild_id in self._healing:
                continue
            if now < self._recover_grace_until.get(guild_id, 0.0):
                continue
            vc = self._clients.get(guild_id)
            if vc is None:
                continue
            if not vc.is_connected():
                # Dead VoiceClient left in _clients after Discord 1006 / failed
                # reconnect — schedule join from DB channel id.
                self._schedule_resume_if_disconnected(
                    guild_id, reason="vc_disconnected"
                )
                continue
            # Keep passthrough enabled; never remount sink while connected.
            try:
                state = getattr(vc, "_connection", None)
                session = getattr(state, "dave_session", None) if state is not None else None
                if session is not None:
                    session.set_passthrough_mode(True, 600)
            except Exception:
                pass

            last_cb = self._last_callback_at.get(guild_id, 0.0)
            last_pcm = self._last_pcm_at.get(guild_id, 0.0)
            cb_age = (now - last_cb) if last_cb > 0 else 1e9
            pcm_age = (now - last_pcm) if last_pcm > 0 else 1e9
            # Zombie: still "connected" but deaf for too long — force hard resume.
            if (
                pcm_age >= VOICE_ZOMBIE_FORCE_RESUME_SECONDS
                and cb_age >= VOICE_ZOMBIE_FORCE_RESUME_SECONDS
            ):
                log.warning(
                    "Voice zombie guild=%s pcm_age=%.0fs cb_age=%.0fs — force resume",
                    guild_id,
                    pcm_age,
                    cb_age,
                )
                self._schedule_zombie_rejoin(guild_id, reason="zombie_no_audio")
                continue

            if status_due:
                empty = self._empty_pcm_streak.get(guild_id, 0)
                fails, oks = _dave_fail_counts()
                log.info(
                    "Voice health guild=%s cb_age=%.0fs pcm_age=%.0fs empty=%s "
                    "dave_fail=%s dave_ok=%s",
                    guild_id,
                    cb_age if cb_age < 1e8 else -1,
                    pcm_age if pcm_age < 1e8 else -1,
                    empty,
                    fails,
                    oks,
                )

        # Also catch guilds that fell out of _clients but still have a DB resume
        # target (failed reconnect can drop the client without leave()).
        if status_due:
            try:
                for config in await self.db.list_guilds():
                    channel_id = config.voice_listen_channel_id
                    if channel_id is None:
                        continue
                    guild_id = config.guild_id
                    if guild_id in tracked and self.is_listening(guild_id):
                        continue
                    if guild_id in self._healing:
                        continue
                    if now < self._recover_grace_until.get(guild_id, 0.0):
                        continue
                    if self.is_listening(guild_id):
                        continue
                    self._schedule_resume_if_disconnected(
                        guild_id, reason="db_resume_missing"
                    )
            except Exception:
                log.exception("Voice health DB resume scan failed")

    async def _transcribe_and_store(
        self,
        bot: ErundaBot,
        guild_id: int,
        user_id: int,
        pcm: bytes,
        buf: _UserBuffer,
    ) -> None:
        try:
            if self.ai.stt_credits_exhausted:
                return
            seconds = len(pcm) / BYTES_PER_SECOND
            rms = pcm_rms(pcm)
            if rms < MIN_PCM_RMS:
                log.info(
                    "STT skip quiet user=%s guild=%s audio=%.1fs rms=%s",
                    user_id,
                    guild_id,
                    seconds,
                    rms,
                )
                return
            wav = await asyncio.to_thread(pcm_to_wav_bytes, pcm)
            text = await self.ai.transcribe_wav(wav)
            text = (text or "").strip()
            meme_source = text
            # Fire soundboard as soon as primary STT matches — don't wait for fallback.
            hit, matched = self._sixseven_from_context(
                guild_id, user_id, meme_source
            )
            if hit:
                log.info(
                    "Sixseven trigger user=%s guild=%s text=%r matched=%r",
                    user_id,
                    guild_id,
                    meme_source[:120],
                    matched[:120],
                )
                await self._play_named_sound(
                    bot, guild_id, SOUNDBOARD_SIXSEVEN_NAME, label="Sixseven"
                )
            erunda_hit = mentions_erunda(meme_source) or mentions_erunda(text)
            if erunda_hit:
                log.info(
                    "Erunda trigger user=%s guild=%s text=%r",
                    user_id,
                    guild_id,
                    meme_source[:120],
                )
                await self._play_named_sound(
                    bot, guild_id, SOUNDBOARD_ERUNDA_NAME, label="Erunda"
                )
            meme_hit = hit or erunda_hit
            # Primary can still mangle memes — second pass only if soundboard missed.
            need_retry = not meme_hit and (
                not text
                or is_hallucination_heavy(text)
                or maybe_sixseven_near_miss(text)
                or maybe_erunda_near_miss(text)
                # Short loud shout is usually the meme; skip quiet «да/ок».
                or (seconds <= 3.5 and rms >= 700 and len(text) <= 32)
            )
            if need_retry:
                retry = await self.ai.transcribe_wav_fallback(wav, language="ru")
                retry = (retry or "").strip()
                if retry and retry != text:
                    log.info(
                        "STT fallback user=%s guild=%s was=%r now=%r",
                        user_id,
                        guild_id,
                        text[:80],
                        retry[:80],
                    )
                    if mentions_sixseven(retry) or mentions_erunda(retry):
                        meme_source = retry
                        if not text or is_hallucination_heavy(text):
                            text = retry
                    elif (
                        not is_hallucination_heavy(retry)
                        and is_useful_transcript(retry)
                        and _cyrillic_letters(retry) >= 4
                    ):
                        text = retry
                        meme_source = retry
                elif not text:
                    retry2 = await self.ai.transcribe_wav_fallback(wav, language=None)
                    retry2 = (retry2 or "").strip()
                    if retry2:
                        text = retry2
                        meme_source = retry2
                if not meme_hit:
                    hit, matched = self._sixseven_from_context(
                        guild_id, user_id, meme_source
                    )
                    if hit:
                        log.info(
                            "Sixseven trigger (fallback) user=%s guild=%s text=%r matched=%r",
                            user_id,
                            guild_id,
                            meme_source[:120],
                            matched[:120],
                        )
                        await self._play_named_sound(
                            bot, guild_id, SOUNDBOARD_SIXSEVEN_NAME, label="Sixseven"
                        )
                    erunda_hit = mentions_erunda(meme_source) or mentions_erunda(text)
                    if erunda_hit:
                        log.info(
                            "Erunda trigger (fallback) user=%s guild=%s text=%r",
                            user_id,
                            guild_id,
                            meme_source[:120],
                        )
                        await self._play_named_sound(
                            bot, guild_id, SOUNDBOARD_ERUNDA_NAME, label="Erunda"
                        )
                    meme_hit = hit or erunda_hit
            if is_hallucination_heavy(text) and not meme_hit:
                stripped = strip_hallucination_phrases(text)
                if is_useful_transcript(stripped) and not is_hallucination_heavy(stripped):
                    text = stripped
                else:
                    self._hallucination_streak[guild_id] += 1
                    hallu = self._hallucination_streak[guild_id]
                    log.info(
                        "STT skip user=%s guild=%s audio=%.1fs rms=%s text=%r hallu=%s",
                        user_id,
                        guild_id,
                        seconds,
                        rms,
                        text[:80],
                        hallu,
                    )
                    # Do not heal/remount mid-call — that desyncs DAVE keys.
                    return
            # Count swears even when we skip storing junk (e.g. thank-you + мат).
            swears = max(count_swears(text), count_swears(meme_source))
            if swears:
                guild = bot.get_guild(guild_id)
                tz_name = "Europe/Moscow"
                if guild is not None:
                    config = await self.db.get_guild(guild_id)
                    if config is not None:
                        tz_name = config.timezone
                day = datetime.now(ZoneInfo(tz_name)).date().isoformat()
                await self.db.increment_voice_swears(guild_id, user_id, day, swears)
                log.info(
                    "Swears +%s user=%s guild=%s text=%r",
                    swears,
                    user_id,
                    guild_id,
                    (text or meme_source)[:80],
                )
            # Meme-only shouts (6-7 / ерунда) have almost no letters — still announce them.
            if not is_useful_transcript(text) and not meme_hit:
                log.info(
                    "STT skip user=%s guild=%s audio=%.1fs rms=%s text=%r",
                    user_id,
                    guild_id,
                    seconds,
                    rms,
                    text[:80],
                )
                return
            if not text and meme_hit:
                text = matched or meme_source
            self._hallucination_streak[guild_id] = 0
            self._soft_rebind_times[guild_id] = []
            self._last_useful_stt_at[guild_id] = datetime.now(timezone.utc).timestamp()
            log.info(
                "STT user=%s guild=%s audio=%.1fs rms=%s text=%r",
                user_id,
                guild_id,
                seconds,
                rms,
                text[:120],
            )
            voice_ch = self.listening_channel(guild_id)
            channel_id = getattr(voice_ch, "id", None) if voice_ch is not None else None
            tz_name = "Europe/Moscow"
            config = await self.db.get_guild(guild_id)
            if config is not None:
                tz_name = config.timezone
            if self.dossier_service is not None:
                await self.dossier_service.record_voice_line(
                    guild_id,
                    user_id,
                    channel_id,
                    text[:2000],
                    timezone_name=tz_name,
                )
            else:
                await self.db.add_voice_snippet(guild_id, user_id, text[:2000])
            await self._announce_transcript(bot, guild_id, user_id, text)
        except OpenRouterRateLimited:
            log.warning(
                "STT rate limited / in-flight budget user=%s guild=%s — keep listening",
                user_id,
                guild_id,
            )
        except OpenRouterCreditsExhausted:
            log.warning("OpenRouter 402 — leaving voice and disabling listen")
            await self.leave_all_credits_exhausted()
        except Exception:
            log.exception("Failed to transcribe voice for user %s", user_id)
        finally:
            buf.flushing = False
            buf.flush_started_at = 0.0

    async def _announce_transcript(
        self,
        bot: ErundaBot,
        guild_id: int,
        user_id: int,
        text: str,
    ) -> None:
        config = await self.db.get_guild(guild_id)
        if config is not None and not config.voice_chat_transcripts:
            log.info("Announce skipped: voice_chat_transcripts off for guild %s", guild_id)
            return
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        channel_id = self._announce_channels.get(guild_id)
        channel = None
        if channel_id is not None:
            channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
            if channel is None and hasattr(bot, "fetch_channel"):
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.HTTPException:
                    channel = None
        # After redeploy announce id is gone — fall back to text-in-voice.
        if channel is None or not hasattr(channel, "send"):
            voice_ch = self.listening_channel(guild_id)
            if voice_ch is not None and hasattr(voice_ch, "send"):
                channel = voice_ch
                channel_id = getattr(voice_ch, "id", None)
                log.info(
                    "Announce fallback to voice channel %s for guild %s",
                    channel_id,
                    guild_id,
                )
        if channel is None or not hasattr(channel, "send"):
            log.info("Announce skipped: no channel set for guild %s", guild_id)
            return
        member = guild.get_member(user_id)
        who = member.display_name if member is not None else f"user {user_id}"
        body = text.strip()
        if len(body) > 1800:
            body = body[:1800] + "…"
        try:
            await channel.send(
                f"**{who}** говорит: {body}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            log.info("Announced transcript in channel %s user=%s", channel_id, user_id)
        except discord.Forbidden:
            log.warning(
                "Cannot post voice transcript in channel %s (missing Send Messages)",
                channel_id,
            )
        except discord.HTTPException:
            log.exception("Failed to post voice transcript in channel %s", channel_id)

    @staticmethod
    def _sound_name_matches(name: str | None, target: str) -> bool:
        cleaned = (name or "").casefold().replace("ё", "е").strip()
        want = (target or "").casefold().replace("ё", "е").strip()
        if not want:
            return False
        return cleaned == want or cleaned.startswith(f"{want} ")

    async def _resolve_named_sound(
        self,
        guild: discord.Guild,
        sound_name: str,
        *,
        force: bool = False,
    ) -> discord.SoundboardSound | None:
        cache_key = (guild.id, sound_name.casefold())
        if not force and cache_key in self._sound_cache:
            cached = self._sound_cache[cache_key]
            if cached is None or self._sound_name_matches(cached.name, sound_name):
                return cached
            self._sound_cache.pop(cache_key, None)
        sounds = list(guild.soundboard_sounds)
        sound = discord.utils.find(
            lambda item: self._sound_name_matches(item.name, sound_name),
            sounds,
        )
        if sound is None:
            try:
                sounds = list(await guild.fetch_soundboard_sounds())
            except discord.HTTPException:
                log.exception("Failed to fetch soundboard sounds for guild %s", guild.id)
                # Do not cache failure forever — boosts/permissions may change.
                return None
            sound = discord.utils.find(
                lambda item: self._sound_name_matches(item.name, sound_name),
                sounds,
            )
        if sound is not None and getattr(sound, "available", True) is False:
            log.warning(
                "Soundboard «%s» exists but unavailable in guild %s (boosts?)",
                sound.name,
                guild.id,
            )
            sound = None
        self._sound_cache[cache_key] = sound
        if sound is None:
            names = ", ".join(sorted({(s.name or "?") for s in sounds})) or "(пусто)"
            log.warning(
                "Soundboard sound «%s» not found in guild %s; available: %s",
                sound_name,
                guild.id,
                names[:300],
            )
        return sound

    async def _resolve_uc_sound(
        self,
        guild: discord.Guild,
        *,
        force: bool = False,
    ) -> discord.SoundboardSound | None:
        """Backward-compat: resolve the sixseven «67» sound."""
        return await self._resolve_named_sound(
            guild, SOUNDBOARD_SIXSEVEN_NAME, force=force
        )

    async def _soundboard_status(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> str:
        if not isinstance(channel, discord.VoiceChannel):
            return "Саундборд: только обычный войс (не stage)."
        me = guild.me
        perms = channel.permissions_for(me) if me is not None else None
        bits: list[str] = []
        for sound_name in (SOUNDBOARD_SIXSEVEN_NAME, SOUNDBOARD_ERUNDA_NAME):
            sound = await self._resolve_named_sound(guild, sound_name, force=True)
            if sound is None:
                bits.append(f"звук «{sound_name}» не найден")
            else:
                bits.append(f"звук «{sound.name}» ок")
        if perms is not None:
            if not perms.speak:
                bits.append("нет права Speak")
            if not perms.use_soundboard:
                bits.append("нет права Use Soundboard")
        return "Саундборд: " + "; ".join(bits)

    def _voice_channel_for_soundboard(
        self,
        guild: discord.Guild,
        guild_id: int,
    ) -> discord.VoiceChannel | None:
        channel = self.listening_channel(guild_id)
        if isinstance(channel, discord.VoiceChannel):
            return channel
        # During voice reconnect `_clients` / vc.channel can be briefly empty
        # while STT still fires from buffered audio.
        me = guild.me
        voice_state = me.voice if me is not None else None
        if voice_state is not None and isinstance(voice_state.channel, discord.VoiceChannel):
            vc = guild.voice_client
            if vc is not None and vc.is_connected():
                self._clients[guild_id] = vc
            return voice_state.channel
        return None

    async def _play_named_sound(
        self,
        bot: ErundaBot,
        guild_id: int,
        sound_name: str,
        *,
        label: str,
    ) -> None:
        if not is_home_guild(guild_id):
            log.info("%s skipped: non-home guild %s", label, guild_id)
            return
        now = datetime.now(timezone.utc).timestamp()
        until = self._sound_cooldown_until.get(guild_id, 0.0)
        if now < until:
            log.info(
                "%s cooldown guild=%s (%.1fs left)",
                label,
                guild_id,
                until - now,
            )
            return
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        channel: discord.VoiceChannel | None = None
        for attempt in range(1, 4):
            channel = self._voice_channel_for_soundboard(guild, guild_id)
            if channel is not None:
                break
            log.warning(
                "%s: voice channel missing guild=%s attempt=%s/%s",
                label,
                guild_id,
                attempt,
                3,
            )
            await asyncio.sleep(0.6 * attempt)
        if not isinstance(channel, discord.VoiceChannel):
            log.warning("%s: not a voice channel in guild %s", label, guild_id)
            return
        # Discord rejects soundboard if the bot is mute/deaf/suppress.
        me = guild.me
        voice_state = me.voice if me is not None else None
        if voice_state is not None and (
            voice_state.self_mute
            or voice_state.self_deaf
            or voice_state.mute
            or voice_state.deaf
            or voice_state.suppress
        ):
            if voice_state.mute or voice_state.deaf or voice_state.suppress:
                log.warning(
                    "Cannot play soundboard in guild %s: bot muted/deaf/suppress by server",
                    guild_id,
                )
                return
            try:
                await guild.change_voice_state(
                    channel=channel,
                    self_mute=False,
                    self_deaf=False,
                )
            except Exception:
                log.exception("Failed to clear self mute/deaf before soundboard")
        sound = await self._resolve_named_sound(guild, sound_name, force=True)
        if sound is None:
            log.warning(
                "%s: sound «%s» missing in guild %s",
                label,
                sound_name,
                guild_id,
            )
            return
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.speak or not perms.use_soundboard:
                log.warning(
                    "No soundboard perms in guild %s channel %s (speak=%s use_soundboard=%s)",
                    guild_id,
                    channel.id,
                    perms.speak,
                    perms.use_soundboard,
                )
                return
        last_exc: BaseException | None = None
        for attempt in range(1, 4):
            try:
                await channel.send_sound(sound)
                self._sound_cooldown_until[guild_id] = (
                    datetime.now(timezone.utc).timestamp() + SOUNDBOARD_COOLDOWN_SECONDS
                )
                log.info(
                    "Played soundboard «%s» in guild %s (attempt=%s, trigger=%s)",
                    sound.name,
                    guild_id,
                    attempt,
                    label,
                )
                return
            except discord.Forbidden:
                log.warning(
                    "Forbidden soundboard in guild %s (need Speak + Use Soundboard, not mute/deaf)",
                    guild_id,
                )
                return
            except discord.HTTPException as exc:
                last_exc = exc
                log.warning(
                    "Soundboard send failed guild=%s attempt=%s/%s: %s",
                    guild_id,
                    attempt,
                    3,
                    exc,
                )
                await asyncio.sleep(0.5 * attempt)
        if last_exc is not None:
            log.exception(
                "Failed to send soundboard sound in guild %s",
                guild_id,
                exc_info=last_exc,
            )

    async def _play_sixseven_sound(self, bot: ErundaBot, guild_id: int) -> None:
        await self._play_named_sound(
            bot, guild_id, SOUNDBOARD_SIXSEVEN_NAME, label="Sixseven"
        )

    @staticmethod
    def aisa_greeting_active(*, now: datetime | None = None) -> bool:
        stamp = now or datetime.now(ZoneInfo("Europe/Moscow"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
        else:
            stamp = stamp.astimezone(ZoneInfo("Europe/Moscow"))
        return stamp.date() <= AISA_GREETING_UNTIL

    @staticmethod
    def is_aisa_member(member: discord.Member | discord.User) -> bool:
        name = (getattr(member, "name", None) or "").casefold()
        global_name = (getattr(member, "global_name", None) or "").casefold()
        target = AISA_USERNAME.casefold()
        return name == target or global_name == target

    def _aisa_disk_cache_key(self, text: str, voice: str) -> bool:
        return (
            voice.casefold().strip() == AISA_GREETING_VOICE
            and text.strip() == AISA_GREETING_TEXT
        )

    def _read_aisa_disk_cache(self) -> bytes | None:
        try:
            if AISA_TTS_CACHE_PATH.is_file():
                data = AISA_TTS_CACHE_PATH.read_bytes()
                if data:
                    return data
        except OSError:
            log.exception("Failed to read Aisa TTS disk cache")
        return None

    def _write_aisa_disk_cache(self, audio: bytes) -> None:
        try:
            AISA_TTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            AISA_TTS_CACHE_PATH.write_bytes(audio)
            log.info("Saved Aisa TTS disk cache (%s bytes)", len(audio))
        except OSError:
            log.exception("Failed to write Aisa TTS disk cache")

    async def synthesize_cached(
        self,
        text: str,
        *,
        voice: str,
    ) -> bytes:
        key = (voice.casefold(), text.strip())
        cached = self._tts_cache.get(key)
        if cached:
            return cached
        if self._aisa_disk_cache_key(text, voice):
            disk = self._read_aisa_disk_cache()
            if disk:
                self._tts_cache[key] = disk
                return disk
        audio = await self.ai.synthesize_speech(text, voice=voice, response_format="pcm")
        # Bound cache size — keep greetings / recent admin lines.
        if len(self._tts_cache) >= 24:
            self._tts_cache.pop(next(iter(self._tts_cache)))
        self._tts_cache[key] = audio
        if self._aisa_disk_cache_key(text, voice):
            self._write_aisa_disk_cache(audio)
        return audio

    @staticmethod
    def _heygen_pcm_to_discord(pcm_bytes: bytes, *, sample_rate: int, channels: int) -> bytes:
        """HeyGen s16le PCM → Discord PCM (48kHz stereo s16le), no ffmpeg."""
        if channels == 1:
            stereo = audioop.tostereo(pcm_bytes, 2, 1.0, 1.0)
        elif channels == 2:
            stereo = pcm_bytes
        else:
            raise ValueError(f"TTS: ожидался mono/stereo, got channels={channels}")
        if sample_rate != PCM_RATE:
            stereo, _ = audioop.ratecv(stereo, 2, 2, sample_rate, PCM_RATE, None)
        # Soft boost — HeyGen can sit a bit quiet after resample.
        return audioop.mul(stereo, 2, 1.35)

    async def play_tts_pcm(
        self,
        guild_id: int,
        pcm_bytes: bytes,
        *,
        label: str = "TTS",
        sample_rate: int = 44100,
        channels: int = 1,
    ) -> None:
        """Play HeyGen raw PCM (s16le mono 44.1kHz) as Discord 48kHz stereo."""
        if not pcm_bytes:
            raise ValueError("Пустое аудио")
        vc = self._clients.get(guild_id)
        if vc is None or not vc.is_connected():
            raise ValueError("Бот не в голосовом канале — сначала /z-admin voice_listen")
        if not isinstance(getattr(vc, "channel", None), discord.VoiceChannel):
            raise ValueError("TTS только в обычном войсе (не stage)")

        discord_pcm = self._heygen_pcm_to_discord(
            pcm_bytes, sample_rate=sample_rate, channels=channels
        )
        if len(discord_pcm) < PCM_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH // 10:
            raise ValueError("TTS слишком короткий после конвертации")

        async with self._tts_play_lock:
            # Same unmute path as soundboard — mute/deaf blocks audible send.
            try:
                voice_state = vc.guild.me.voice if vc.guild.me else None
                if voice_state is not None and (
                    voice_state.self_mute
                    or voice_state.self_deaf
                    or voice_state.mute
                    or voice_state.deaf
                    or voice_state.suppress
                ):
                    if voice_state.mute or voice_state.deaf or voice_state.suppress:
                        raise ValueError("Бот заглушен сервером — TTS не услышать")
                    await vc.guild.change_voice_state(
                        channel=vc.channel,  # type: ignore[arg-type]
                        self_mute=False,
                        self_deaf=False,
                    )
                    await asyncio.sleep(0.2)
            except ValueError:
                raise
            except Exception:
                log.exception("Failed to clear self mute/deaf before TTS")

            if vc.is_playing():
                vc.stop()
            await asyncio.sleep(0.25)

            source = discord.PCMAudio(io.BytesIO(discord_pcm))
            done = asyncio.Event()
            error_box: list[BaseException] = []

            def _after(err: BaseException | None) -> None:
                if err is not None:
                    error_box.append(err)
                bot = self._bot
                if bot is not None:
                    bot.loop.call_soon_threadsafe(done.set)
                else:
                    done.set()

            state = getattr(vc, "_connection", None)
            can_encrypt = getattr(state, "can_encrypt", None)
            encrypt_ok = bool(can_encrypt()) if callable(can_encrypt) else None
            vc.play(source, after=_after)
            log.info(
                "Playing TTS «%s» guild=%s in=%s out=%s rate=%s ch=%s encrypt=%s",
                label,
                guild_id,
                len(pcm_bytes),
                len(discord_pcm),
                sample_rate,
                channels,
                encrypt_ok,
            )
            try:
                await asyncio.wait_for(done.wait(), timeout=120.0)
            except asyncio.TimeoutError:
                if vc.is_playing():
                    vc.stop()
                raise TimeoutError("TTS playback timed out") from None
            if error_box:
                raise error_box[0]

    async def play_tts_text(
        self,
        guild_id: int,
        text: str,
        *,
        voice: str,
    ) -> str:
        voice_id = voice.casefold().strip()
        if voice_id not in HEYGEN_VOICES:
            raise ValueError(
                f"Неизвестный голос «{voice}». Доступны: {', '.join(HEYGEN_VOICES)}"
            )
        audio = await self.synthesize_cached(text, voice=voice_id)
        await self.play_tts_pcm(
            guild_id,
            audio,
            label=f"{voice_id}: {text[:40]}",
        )
        return voice_id

    async def handle_aisa_voice_greeting(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Until 30.11: greet @i_aisa with Gareth TTS when she joins the bot's VC."""
        if member.bot or not self.is_aisa_member(member):
            return
        if not self.aisa_greeting_active():
            return
        if after.channel is None:
            return
        # Joined or switched into a channel.
        if before.channel is not None and before.channel.id == after.channel.id:
            return
        guild_id = member.guild.id
        if not self.is_listening(guild_id):
            return
        listening = self.listening_channel(guild_id)
        if listening is None or listening.id != after.channel.id:
            return
        now = datetime.now(timezone.utc).timestamp()
        until = self._aisa_greeting_cooldown_until.get(guild_id, 0.0)
        if now < until:
            return
        self._aisa_greeting_cooldown_until[guild_id] = now + AISA_GREETING_COOLDOWN_SECONDS
        try:
            await asyncio.sleep(AISA_GREETING_DELAY_SECONDS)
            # She may have left during the delay.
            current = member.voice
            if (
                current is None
                or current.channel is None
                or listening is None
                or current.channel.id != listening.id
                or not self.is_listening(guild_id)
            ):
                return
            await self.play_tts_text(
                guild_id,
                AISA_GREETING_TEXT,
                voice=AISA_GREETING_VOICE,
            )
            log.info(
                "Aisa greeting played guild=%s user=%s",
                guild_id,
                member.id,
            )
        except Exception:
            log.exception(
                "Aisa greeting failed guild=%s user=%s",
                guild_id,
                member.id,
            )

    async def process_daily_dossiers(self, bot: ErundaBot) -> None:
        """Backward-compatible entry; real work lives in DossierService."""
        if self.dossier_service is not None:
            await self.dossier_service.process_daily_and_weekly(bot)
