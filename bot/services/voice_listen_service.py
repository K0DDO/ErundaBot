"""Join voice, receive PCM, transcribe via Groq Whisper, store snippets/dossier."""

from __future__ import annotations

import asyncio
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
SILENCE_FLUSH_SECONDS = 1.4
MIN_AUDIO_SECONDS = 0.6
MAX_BUFFER_SECONDS = 12.0
BYTES_PER_SECOND = PCM_RATE * PCM_CHANNELS * PCM_SAMPLE_WIDTH

# Compact RU swear stems; count after STT for /stats later.
SWEAR_RE = re.compile(
    r"(?i)(?<![а-яёa-z])"
    r"(бл[яа][дт]|бля|сука|хуй|хуя|хуе|пизд|ёб|еб[аоуы]|еба|ебл|муд[ае]|гандон|залуп|пидор|пидр)"
    r"[а-яёa-z]*"
)
# Whisper often mangles the meme: 67, six7, сикс севен, шесть семь, etc.
SIXSEVEN_RE = re.compile(
    r"(?i)(?:"
    r"six\s*-?\s*seven|"
    r"six\s*7|"
    r"(?<!\d)6\s*7(?!\d)|"
    r"сикс\s*-?\s*севен|"
    r"сик\s*-?\s*севен|"
    r"сиксевен|"
    r"секс\s*-?\s*севен|"
    r"шесть\s*-?\s*семь"
    r")"
)
# Whisper noise / music hallucinations on bad audio.
JUNK_TRANSCRIPT_RE = re.compile(
    r"(?is)^(?:[\s\-–—*.,…·•]+|\*[^*]{0,40}\*|♪+|\[.*?\]|\(.*?\))$"
)
SOUNDBOARD_TRIGGER_NAME = "уц"
SOUNDBOARD_COOLDOWN_SECONDS = 4.0
_VOICE_RECV_PATCHED = False


def count_swears(text: str) -> int:
    return len(SWEAR_RE.findall(text or ""))


def mentions_sixseven(text: str) -> bool:
    return bool(SIXSEVEN_RE.search(text or ""))


def is_useful_transcript(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 2:
        return False
    if JUNK_TRANSCRIPT_RE.match(cleaned):
        return False
    letters = sum(1 for ch in cleaned if ch.isalpha())
    return letters >= 2


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(PCM_CHANNELS)
        wav.setsampwidth(PCM_SAMPLE_WIDTH)
        wav.setframerate(PCM_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


def _patch_voice_recv_router() -> None:
    """Keep listening when a single opus packet is corrupt.

    discord-ext-voice-recv's PacketRouter.run() always stop_listening() on any
    exception. Without this patch one bad frame kills the whole session.
    """
    global _VOICE_RECV_PATCHED
    if _VOICE_RECV_PATCHED:
        return
    from discord.ext.voice_recv import router as vr_router

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

    vr_router.PacketRouter._do_run = _do_run  # type: ignore[method-assign]
    _VOICE_RECV_PATCHED = True
    log.info("Patched voice_recv PacketRouter to survive OpusError")


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

    def is_listening(self, guild_id: int) -> bool:
        vc = self._clients.get(guild_id)
        return vc is not None and vc.is_connected()

    def listening_channel(self, guild_id: int) -> discord.abc.Connectable | None:
        vc = self._clients.get(guild_id)
        if vc is None or not vc.is_connected():
            return None
        return vc.channel

    async def start(self, bot: ErundaBot) -> None:
        self._bot = bot
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._silence_watchdog(bot))

    async def stop_all(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None
        for guild_id in list(self._clients):
            await self.leave(guild_id)

    async def join(
        self,
        bot: ErundaBot,
        channel: discord.VoiceChannel | discord.StageChannel,
        *,
        announce_channel: discord.abc.Messageable | None = None,
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

        _patch_voice_recv_router()

        guild = channel.guild
        async with self._lock:
            existing = guild.voice_client
            if existing is not None and existing.is_connected():
                if existing.channel and existing.channel.id == channel.id:
                    if guild.id not in self._clients:
                        self._clients[guild.id] = existing
                    if announce_channel is not None and getattr(announce_channel, "id", None):
                        self._announce_channels[guild.id] = int(announce_channel.id)
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
            if announce_channel is not None and getattr(announce_channel, "id", None):
                self._announce_channels[guild.id] = int(announce_channel.id)

            def on_voice(user: discord.User | discord.Member | None, data) -> None:
                user_id = self._resolve_speaker_id(vc, user, data)
                if user_id is None:
                    self._unknown_speaker_packets[guild.id] += 1
                    if self._unknown_speaker_packets[guild.id] in (1, 50, 200):
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
            await self.start(bot)
            tip = await self._soundboard_status(guild, channel)
            where = ""
            if guild.id in self._announce_channels:
                where = f"\nРеплики пишу в <#{self._announce_channels[guild.id]}>"
            return f"Слушаю {channel.mention}\n{tip}{where}"

    async def leave(self, guild_id: int, *, flush: bool = True) -> str:
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
        if vc is not None and vc.is_connected():
            try:
                vc.stop_listening()
            except Exception:
                pass
            await vc.disconnect(force=True)
            return "Вышел из войса"
        return "Бот не был в войсе"

    @staticmethod
    def _resolve_speaker_id(
        vc: discord.VoiceProtocol,
        user: discord.User | discord.Member | None,
        data: object,
    ) -> int | None:
        if user is not None:
            if getattr(user, "bot", False):
                return None
            return int(user.id)
        packet = getattr(data, "packet", None)
        ssrc = getattr(packet, "ssrc", None)
        if ssrc is not None:
            getter = getattr(vc, "_get_id_from_ssrc", None)
            if callable(getter):
                try:
                    user_id = getter(ssrc)
                except Exception:
                    user_id = None
                if user_id is not None:
                    return int(user_id)
        # Speaking-map sometimes lags; if only one human is in the channel, use them.
        channel = getattr(vc, "channel", None)
        members = getattr(channel, "members", None) or []
        humans = [m for m in members if not getattr(m, "bot", False)]
        if len(humans) == 1:
            return int(humans[0].id)
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
            wav = await asyncio.to_thread(pcm_to_wav_bytes, pcm)
            text = await self.ai.transcribe_wav(wav)
            text = (text or "").strip()
            if not is_useful_transcript(text):
                log.info(
                    "STT skip user=%s guild=%s audio=%.1fs text=%r",
                    user_id,
                    guild_id,
                    seconds,
                    text[:80],
                )
                return
            log.info(
                "STT user=%s guild=%s audio=%.1fs text=%r",
                user_id,
                guild_id,
                seconds,
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
            if mentions_sixseven(text):
                log.info(
                    "Sixseven trigger user=%s guild=%s text=%r",
                    user_id,
                    guild_id,
                    text[:120],
                )
                await self._play_sixseven_sound(bot, guild_id)
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
        channel_id = self._announce_channels.get(guild_id)
        if channel_id is None:
            return
        config = await self.db.get_guild(guild_id)
        if config is not None and not config.voice_chat_transcripts:
            return
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id) or bot.get_channel(channel_id)
        if channel is None or not hasattr(channel, "send"):
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
