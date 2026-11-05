"""Join voice, receive PCM, transcribe via Groq Whisper, store snippets/dossier."""

from __future__ import annotations

import asyncio
import audioop
import io
import logging
import re
import wave
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord.opus import OpusError

from bot.database.database import Database
from bot.services.ai_service import OpenRouterCreditsExhausted

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
SILENCE_FLUSH_SECONDS = 2.0
MIN_AUDIO_SECONDS = 0.6
MAX_BUFFER_SECONDS = 12.0
BYTES_PER_SECOND = PCM_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH
# Join nearby STT chunks so "сикс" + "севен" across a short pause still match.
SIXSEVEN_CONTEXT_SECONDS = 3.5
SIXSEVEN_CONTEXT_MAX_CHUNKS = 4

# Compact RU swear stems; count after STT for /stats later.
SWEAR_RE = re.compile(
    r"(?i)(?<![а-яёa-z])"
    r"(бл[яа][дт]|бля|сука|хуй|хуя|хуе|пизд|ёб|еб[аоуы]|еба|ебл|муд[ае]|гандон|залуп|пидор|пидр)"
    r"[а-яёa-z]*"
)
# Whisper mangles the meme hard: 6-7, сек-севен, сексаван, sex seven, …
SIXSEVEN_RE = re.compile(
    r"(?:"
    r"six\s*[-–—./]?\s*seven|"
    r"six\s*7|"
    r"6\s*seven|"
    r"sex\s*[-–—./]?\s*seven|"
    r"(?<!\d)6\s*[-–—./]?\s*7(?!\d)|"
    r"сикс\s*[-–—./]?\s*севен|"
    r"сик\s*с?\s*[-–—./]?\s*севен|"
    r"сикс[аеэ]?в[аеэ]?н|"
    r"сикс[ао]?в[аеэ]н|"
    r"сек\s*с?\s*[-–—./]?\s*севен|"
    r"секс\s*[-–—./]?\s*севен|"
    r"секс[ао]?в[аеэ]н|"
    r"сексев[аеэ]н|"
    r"сыкс\s*[-–—./]?\s*севен|"
    r"сакс\s*[-–—./]?\s*севен|"
    r"шесть\s*[-–—./]?\s*семь|"
    r"6\s*семь|"
    r"шесть\s*7"
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
    # Empty-room / noise often becomes only «Спасибо» spam.
    r"^(?:спасибо[.!]?\s*)+$"
)
SOUNDBOARD_TRIGGER_NAME = "уц"
SOUNDBOARD_COOLDOWN_SECONDS = 4.0
# 16-bit PCM RMS below this ≈ silence / decrypt garbage.
MIN_PCM_RMS = 250
_VOICE_RECV_PATCHED = False


def count_swears(text: str) -> int:
    return len(SWEAR_RE.findall(text or ""))


def mentions_sixseven(text: str) -> bool:
    cleaned = (text or "").casefold().replace("ё", "е")
    # Whisper often sticks punctuation to digits/words: «6-7.», «Сек-севен?»
    cleaned = re.sub(r"[\"'«»„“”]", "", cleaned)
    return bool(SIXSEVEN_RE.search(cleaned))


def is_hallucination_heavy(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if HALLUCINATION_RE.search(cleaned):
        return True
    return False


def is_useful_transcript(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 2:
        return False
    if JUNK_TRANSCRIPT_RE.match(cleaned):
        return False
    if HALLUCINATION_RE.search(cleaned):
        return False
    letters = sum(1 for ch in cleaned if ch.isalpha())
    return letters >= 2


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


def _patch_voice_recv() -> None:
    """DAVE decrypt (PR#54-style) + keep listening on bad opus frames.

    Discord mandates DAVE E2EE. Stock voice-recv only opens the outer envelope;
    ciphertext → OpusError / Whisper hallucinations. We decrypt with discord.py's
    dave_session before opus decode, enable passthrough while MLS keys settle,
    and stop PacketRouter from killing the whole listen on one bad frame.
    """
    global _VOICE_RECV_PATCHED
    if _VOICE_RECV_PATCHED:
        return

    import davey
    from davey import MediaType
    from discord.ext.voice_recv import opus as vr_opus
    from discord.ext.voice_recv import router as vr_router
    from discord.opus import Decoder as OpusDecoder

    orig_init = vr_opus.PacketDecoder.__init__
    dave_fails = {"n": 0}
    dave_ok = {"n": 0}

    def _init(self, router, ssrc):  # type: ignore[no-untyped-def]
        orig_init(self, router, ssrc)
        try:
            vc = self.sink.voice_client
            session = getattr(getattr(vc, "_connection", None), "dave_session", None)
            if session is not None:
                session.set_passthrough_mode(True, 120)
        except Exception:
            log.exception("DAVE set_passthrough_mode on decoder init failed")

    def _process_packet(self, packet):  # type: ignore[no-untyped-def]
        # Mirrors imayhaveborkedit/discord-ext-voice-recv#54 with None-member guard.
        pcm = None
        member = self._get_cached_member()
        if member is None:
            try:
                self._cached_id = self.sink.voice_client._get_id_from_ssrc(self.ssrc)
            except Exception:
                self._cached_id = None
            member = self._get_cached_member()

        vc = self.sink.voice_client
        state = getattr(vc, "_connection", None)
        session = getattr(state, "dave_session", None) if state is not None else None
        silence = bool(getattr(packet, "is_silence", lambda: False)())
        payload = getattr(packet, "payload_type", getattr(packet, "type", 120))
        raw = getattr(packet, "decrypted_data", None)

        if (
            session is not None
            and getattr(session, "ready", False)
            and packet
            and not silence
            and raw is not None
            and len(raw) > 3
            and member is not None
            and int(payload) == 120
        ):
            try:
                packet.decrypted_data = session.decrypt(
                    int(member.id),
                    MediaType.audio,
                    bytes(raw),
                )
                dave_ok["n"] += 1
                if dave_ok["n"] == 1:
                    users = []
                    try:
                        users = list(session.get_user_ids())
                    except Exception:
                        pass
                    log.info(
                        "DAVE decrypt ok (first) user=%s mls_users=%s",
                        member.id,
                        users,
                    )
            except Exception as exc:
                dave_fails["n"] += 1
                if dave_fails["n"] in (1, 50, 200, 1000):
                    users = []
                    try:
                        users = list(session.get_user_ids())
                    except Exception:
                        pass
                    log.warning(
                        "DAVE decrypt failed (#%s, user=%s, ready=%s, mls_users=%s): %r",
                        dave_fails["n"],
                        member.id if member else None,
                        getattr(session, "ready", None),
                        users,
                        exc,
                    )
                # Drop frame as silence so opus/router stay alive.
                self._last_seq = packet.sequence
                self._last_ts = packet.timestamp
                return vr_opus.VoiceData(packet, member, pcm=b"")

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
            session.set_passthrough_mode(True, 120)
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
    __slots__ = ("pcm", "last_packet_at", "flushing")

    def __init__(self) -> None:
        self.pcm = bytearray()
        self.last_packet_at = 0.0
        self.flushing = False


class VoiceListenService:
    def __init__(self, db: Database, ai: AIService) -> None:
        self.db = db
        self.ai = ai
        self._clients: dict[int, discord.VoiceProtocol] = {}
        self._buffers: dict[int, dict[int, _UserBuffer]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._dossier_dates: dict[int, date] = {}
        self._bot: ErundaBot | None = None
        self._credits_shutdown = False
        self._sound_cache: dict[int, discord.SoundboardSound | None] = {}
        self._sound_cooldown_until: dict[int, float] = {}
        self._announce_channels: dict[int, int] = {}
        self._pcm_packets: dict[int, int] = defaultdict(int)
        self._unknown_speaker_packets: dict[int, int] = defaultdict(int)
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
        asyncio.create_task(self._restore_sessions(bot))

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
                    self._ensure_watchdog(bot)
                    return f"Уже слушаю {channel.mention}"
                await existing.disconnect(force=True)

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
            self._pcm_packets[guild.id] = 0
            self._unknown_speaker_packets[guild.id] = 0
            if announce_channel_id is not None:
                self._announce_channels[guild.id] = int(announce_channel_id)
                log.info(
                    "Voice announce channel for guild %s → %s",
                    guild.id,
                    announce_channel_id,
                )
            await _wait_for_dave(vc)

            def on_voice(user: discord.User | discord.Member | None, data) -> None:
                user_id = self._resolve_speaker_id(guild.id, vc, user, data)
                if user_id is None:
                    self._unknown_speaker_packets[guild.id] += 1
                    if self._unknown_speaker_packets[guild.id] in (1, 50, 200, 1000):
                        log.warning(
                            "Voice packets without speaker map in guild %s (n=%s)",
                            guild.id,
                            self._unknown_speaker_packets[guild.id],
                        )
                    return
                pcm = getattr(data, "pcm", None)
                if not pcm:
                    return
                self._pcm_packets[guild.id] += 1
                if self._pcm_packets[guild.id] == 1:
                    log.info("First voice PCM packet in guild %s user=%s", guild.id, user_id)
                loop = bot.loop
                loop.call_soon_threadsafe(
                    self._append_pcm,
                    guild.id,
                    user_id,
                    bytes(pcm),
                )

            # Library decode (with FEC). Router is patched to survive OpusError.
            vc.listen(voice_recv.BasicSink(on_voice, decode=True))
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
            await vc.disconnect(force=True)
            return "Вышел из войса"
        return "Бот не был в войсе"

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
        # Speaking-map sometimes lags; prefer a single unmuted human.
        channel = getattr(vc, "channel", None)
        members = getattr(channel, "members", None) or []
        humans = [m for m in members if not getattr(m, "bot", False)]
        if len(humans) == 1:
            return int(humans[0].id)
        live = []
        for member in humans:
            state = getattr(member, "voice", None)
            if state is None:
                continue
            if getattr(state, "self_mute", False) or getattr(state, "mute", False):
                continue
            live.append(member)
        if len(live) == 1:
            return int(live[0].id)
        return None

    async def leave_all_credits_exhausted(self) -> None:
        """Stop listening everywhere after OpenRouter 402."""
        if self._credits_shutdown:
            return
        self._credits_shutdown = True
        self.ai.mark_stt_credits_exhausted()
        for guild_id in list(self._clients):
            try:
                await self.leave(guild_id, flush=False)
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
            buf.flushing = True
            # Flush scheduled by watchdog with bot; mark for immediate attention.
            buf.last_packet_at = 0.0

    async def _silence_watchdog(self, bot: ErundaBot) -> None:
        await bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(0.4)
                now = datetime.now(timezone.utc).timestamp()
                for guild_id, users in list(self._buffers.items()):
                    for user_id, buf in list(users.items()):
                        if buf.flushing or not buf.pcm:
                            continue
                        silent_for = now - buf.last_packet_at
                        max_bytes = int(MAX_BUFFER_SECONDS * BYTES_PER_SECOND)
                        if silent_for < SILENCE_FLUSH_SECONDS and len(buf.pcm) < max_bytes:
                            continue
                        if len(buf.pcm) < int(MIN_AUDIO_SECONDS * BYTES_PER_SECOND):
                            buf.pcm.clear()
                            continue
                        buf.flushing = True
                        pcm = bytes(buf.pcm)
                        buf.pcm.clear()
                        asyncio.create_task(
                            self._transcribe_and_store(bot, guild_id, user_id, pcm, buf)
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Voice silence watchdog failed")

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
            # RU-locked Whisper often drops the meme into «Продолжение следует».
            if text and is_hallucination_heavy(text) and not mentions_sixseven(text):
                retry = await self.ai.transcribe_wav(wav, language=None)
                retry = (retry or "").strip()
                if retry and retry != text:
                    log.info(
                        "STT retry no-lang user=%s guild=%s was=%r now=%r",
                        user_id,
                        guild_id,
                        text[:80],
                        retry[:80],
                    )
                    if mentions_sixseven(retry) or not is_hallucination_heavy(retry):
                        text = retry
            hit, matched = self._sixseven_from_context(guild_id, user_id, text)
            if hit:
                log.info(
                    "Sixseven trigger user=%s guild=%s text=%r matched=%r",
                    user_id,
                    guild_id,
                    text[:120],
                    matched[:120],
                )
                await self._play_sixseven_sound(bot, guild_id)
            if not is_useful_transcript(text):
                log.info(
                    "STT skip user=%s guild=%s audio=%.1fs rms=%s text=%r",
                    user_id,
                    guild_id,
                    seconds,
                    rms,
                    text[:80],
                )
                return
            log.info(
                "STT user=%s guild=%s audio=%.1fs rms=%s text=%r",
                user_id,
                guild_id,
                seconds,
                rms,
                text[:120],
            )
            await self.db.add_voice_snippet(guild_id, user_id, text[:2000])
            await self._announce_transcript(bot, guild_id, user_id, text)
            swears = count_swears(text)
            if swears:
                guild = bot.get_guild(guild_id)
                tz_name = "Europe/Moscow"
                if guild is not None:
                    config = await self.db.get_guild(guild_id)
                    if config is not None:
                        tz_name = config.timezone
                day = datetime.now(ZoneInfo(tz_name)).date().isoformat()
                await self.db.increment_voice_swears(guild_id, user_id, day, swears)
        except OpenRouterCreditsExhausted:
            log.warning("OpenRouter 402 — leaving voice and disabling listen")
            await self.leave_all_credits_exhausted()
        except Exception:
            log.exception("Failed to transcribe voice for user %s", user_id)
        finally:
            buf.flushing = False

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
        who = member.mention if member is not None else f"<@{user_id}>"
        body = text.strip()
        if len(body) > 1800:
            body = body[:1800] + "…"
        try:
            await channel.send(
                f"{who} говорит: {body}",
                allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(id=user_id)],
                    roles=False,
                    everyone=False,
                ),
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
    def _sound_name_matches(name: str | None) -> bool:
        cleaned = (name or "").casefold().replace("ё", "е").strip()
        target = SOUNDBOARD_TRIGGER_NAME.casefold()
        return cleaned == target or cleaned.startswith(f"{target} ")

    async def _resolve_uc_sound(
        self,
        guild: discord.Guild,
        *,
        force: bool = False,
    ) -> discord.SoundboardSound | None:
        if not force and guild.id in self._sound_cache:
            return self._sound_cache[guild.id]
        sounds = list(guild.soundboard_sounds)
        sound = discord.utils.find(lambda item: self._sound_name_matches(item.name), sounds)
        if sound is None:
            try:
                sounds = list(await guild.fetch_soundboard_sounds())
            except discord.HTTPException:
                log.exception("Failed to fetch soundboard sounds for guild %s", guild.id)
                # Do not cache failure forever — boosts/permissions may change.
                return None
            sound = discord.utils.find(
                lambda item: self._sound_name_matches(item.name),
                sounds,
            )
        if sound is not None and getattr(sound, "available", True) is False:
            log.warning(
                "Soundboard «%s» exists but unavailable in guild %s (boosts?)",
                sound.name,
                guild.id,
            )
            sound = None
        self._sound_cache[guild.id] = sound
        if sound is None:
            names = ", ".join(sorted({(s.name or "?") for s in sounds})) or "(пусто)"
            log.warning(
                "Soundboard sound «%s» not found in guild %s; available: %s",
                SOUNDBOARD_TRIGGER_NAME,
                guild.id,
                names[:300],
            )
        return sound

    async def _soundboard_status(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> str:
        if not isinstance(channel, discord.VoiceChannel):
            return "Саундборд: только обычный войс (не stage)."
        me = guild.me
        perms = channel.permissions_for(me) if me is not None else None
        sound = await self._resolve_uc_sound(guild, force=True)
        bits: list[str] = []
        if sound is None:
            bits.append(f"звук «{SOUNDBOARD_TRIGGER_NAME}» не найден на сервере")
        else:
            bits.append(f"звук «{sound.name}» ок")
        if perms is not None:
            if not perms.speak:
                bits.append("нет права Speak")
            if not perms.use_soundboard:
                bits.append("нет права Use Soundboard")
        return "Саундборд: " + "; ".join(bits)

    async def _play_sixseven_sound(self, bot: ErundaBot, guild_id: int) -> None:
        now = datetime.now(timezone.utc).timestamp()
        until = self._sound_cooldown_until.get(guild_id, 0.0)
        if now < until:
            return
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        channel = self.listening_channel(guild_id)
        if not isinstance(channel, discord.VoiceChannel):
            log.warning("Sixseven: not a voice channel in guild %s", guild_id)
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
        sound = await self._resolve_uc_sound(guild)
        if sound is None:
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
        try:
            await channel.send_sound(sound)
            self._sound_cooldown_until[guild_id] = now + SOUNDBOARD_COOLDOWN_SECONDS
            log.info("Played soundboard «%s» in guild %s", sound.name, guild_id)
        except discord.Forbidden:
            log.warning(
                "Forbidden soundboard in guild %s (need Speak + Use Soundboard, not mute/deaf)",
                guild_id,
            )
        except discord.HTTPException:
            log.exception("Failed to send soundboard sound in guild %s", guild_id)

    async def process_daily_dossiers(self, bot: ErundaBot) -> None:
        """Once per guild local day: summarize pending snippets into dossier facts."""
        try:
            guilds = await self.db.list_guilds()
        except Exception:
            log.exception("Failed to load guilds for voice dossier")
            return

        for config in guilds:
            guild = bot.get_guild(config.guild_id)
            if guild is None:
                continue
            local_now = datetime.now(timezone.utc).astimezone(ZoneInfo(config.timezone))
            # Same window style as TGK refresh: 04:10 local.
            if local_now.hour != 4 or local_now.minute != 10:
                continue
            if self._dossier_dates.get(config.guild_id) == local_now.date():
                continue
            try:
                await self._process_guild_dossiers(guild, local_now.date())
                await self.db.cleanup_old_voice_snippets(keep_days=14)
                self._dossier_dates[config.guild_id] = local_now.date()
            except Exception:
                log.exception("Voice dossier failed for guild %s", config.guild_id)

    async def _process_guild_dossiers(self, guild: discord.Guild, today: date) -> None:
        user_ids = await self.db.list_pending_voice_snippet_users(guild.id)
        for user_id in user_ids:
            snippets = await self.db.list_pending_voice_snippets(guild.id, user_id)
            if not snippets:
                continue
            member = guild.get_member(user_id)
            name = member.display_name if member else f"user {user_id}"
            joined = "\n".join(f"- {item.text}" for item in snippets[:60])
            facts = await self.ai.summarize_voice_dossier(
                display_name=name,
                lines=joined,
            )
            if facts:
                await self.db.add_voice_dossier_entries(
                    guild.id,
                    user_id,
                    facts[:5],
                    today.isoformat(),
                )
            await self.db.mark_voice_snippets_processed([item.id for item in snippets])
