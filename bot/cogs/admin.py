"""Staff slash commands: /z-admin (owner|guild admin) and /z-test-admin (owner only).

Discord forbids '.' in slash names; 'z-' keeps them near the end of the picker.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.models import Birthday
from bot.services.ai_service import (
    HEYGEN_VOICES,
    OpenRouterCreditsExhausted,
    OpenRouterRateLimited,
)
from bot.services.festival_service import normalize_film_title
from bot.services.voice_listen_service import (
    AISA_GREETING_TEXT,
    AISA_GREETING_UNTIL,
    AISA_GREETING_VOICE,
    AISA_USERNAME,
)
from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.settings import voice_listen_allowed
from bot.utils.permissions import (
    can_staff,
    is_owner,
    owner_denied_reason,
    staff_denied_reason,
)
from bot.views.config_views import ConfigPanel, config_overview_embed
from bot.views.santa_views import publish_santa_message, refresh_santa_message

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    admin = app_commands.Group(
        name="z-admin",
        description="Админские команды",
    )
    test_admin = app_commands.Group(
        name="z-test-admin",
        description="Служебные команды",
    )

    def _require_staff(self, interaction: discord.Interaction) -> str | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return "Только на сервере"
        if not can_staff(interaction.user):
            return staff_denied_reason()
        return None

    def _require_owner(self, interaction: discord.Interaction) -> str | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return "Только на сервере"
        if not is_owner(interaction.user):
            return owner_denied_reason()
        return None

    @admin.command(name="config", description="Настройки Ерунды для этого сервера")
    @app_commands.guild_only()
    async def admin_config(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed("Недостаточно прав", denied),
                ephemeral=True,
            )
            return
        assert interaction.guild is not None
        config = await self.bot.config_service.get(interaction.guild.id)
        view = ConfigPanel(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=config_overview_embed(config),
            view=view,
            ephemeral=True,
        )

    @admin.command(name="birthday_test", description="Тестовое поздравление")
    @app_commands.describe(user="Кого поздравить, по умолчанию тебя")
    @app_commands.guild_only()
    async def admin_birthday_test(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        config = await self.bot.config_service.get(interaction.guild.id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        target = user or interaction.user
        today = datetime.now(ZoneInfo(config.timezone)).date()
        stored = await self.bot.birthday_service.get_birthday(interaction.guild.id, target.id)
        birthday = Birthday(
            guild_id=interaction.guild.id,
            user_id=target.id,
            day=today.day,
            month=today.month,
            year=stored.year if stored is not None else None,
        )
        try:
            embed, used_ai = await self.bot.birthday_service.announce_embed(
                interaction.guild,
                birthday,
                today,
                self.bot.ai_service,
            )
        except Exception:
            log.exception("Birthday test greeting failed")
            await interaction.followup.send(
                embed=error_embed("Не получилось собрать поздравление"),
                ephemeral=True,
            )
            return
        note = "Сгенерировано ИИ" if used_ai else "ИИ не ответил — запасной текст"
        role_note = "роль не трогали"
        if isinstance(target, discord.Member):
            try:
                role_note = await self.bot.birthday_star_service.grant(
                    interaction.guild,
                    target,
                    granted_on=today,
                    force=True,
                )
            except Exception as exc:
                log.exception("Birthday test star grant failed")
                role_note = f"роль не выдана: {exc}"
        embed.set_footer(text=f"Тест · {note} · {role_note}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin.command(name="quote_cleanup", description="Почистить канал цитат и переназначить номера")
    @app_commands.guild_only()
    async def admin_quote_cleanup(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        config = await self.bot.config_service.get(interaction.guild.id)
        if config.quotes_channel_id is None:
            await interaction.response.send_message(
                embed=error_embed("Канал цитат не задан в /z-admin config"),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(config.quotes_channel_id)
        if channel is None or not hasattr(channel, "history"):
            await interaction.response.send_message(
                embed=error_embed("Канал цитат не найден"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        removed = await self.bot.quote_service.cleanup_channel(
            interaction.guild, channel  # type: ignore[arg-type]
        )
        numbered = await self.bot.quote_service.renumber_and_sync(interaction.guild)
        await interaction.followup.send(
            embed=success_embed(
                "Цитаты почищены",
                f"Удалено сообщений: **{removed}** в {channel.mention}\n"
                f"Номера переназначены: **{numbered}**, старше — меньше номер.",
            ),
        )

    @admin.command(name="fest_stats", description="Оценки и присутствие на сеансе")
    @app_commands.describe(number="Номер кинофестиваля")
    @app_commands.guild_only()
    async def admin_fest_stats(self, interaction: discord.Interaction, number: int) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
            festival = await self.bot.festival_service.require_by_number(
                interaction.guild.id,
                number,
            )
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        try:
            await self.bot.festival_presence_service.sync_guild(interaction.guild)
        except Exception:
            log.exception("Festival presence sync before stats failed")
        logs = await self.bot.db.list_festival_rating_logs(festival.id)
        ratings = await self.bot.db.list_festival_ratings(festival.id)
        now_iso = datetime.now(timezone.utc).isoformat()
        voice_stats = await self.bot.db.festival_voice_stats(festival.id, now_iso)
        text = self.bot.festival_service.format_fest_stats(
            festival,
            logs,
            ratings,
            voice_stats,
            interaction.guild,
        )
        chunks = self._split_message(text, 3900)
        await interaction.response.send_message(
            embed=discord.Embed(description=chunks[0], color=0x7C9CFF),
            ephemeral=True,
        )
        for chunk in chunks[1:]:
            await interaction.followup.send(
                embed=discord.Embed(description=chunk, color=0x7C9CFF),
                ephemeral=True,
            )

    @admin_fest_stats.autocomplete("number")
    async def admin_fest_stats_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        festivals = await self.bot.db.list_guild_festivals(interaction.guild.id)
        choices: list[app_commands.Choice[int]] = []
        needle = current.strip().lower()
        for festival in festivals:
            label = f"#{festival.number}"
            if festival.winner_film:
                label += f" · {normalize_film_title(festival.winner_film)}"
            elif festival.status == "open":
                label += " · текущий"
            hay = label.lower()
            if needle and needle not in hay and needle not in str(festival.number):
                continue
            choices.append(app_commands.Choice(name=label[:100], value=festival.number))
            if len(choices) >= 25:
                break
        return choices

    @admin.command(name="santa_start", description="Новый тайный Санта — пустая карточка в канал")
    @app_commands.guild_only()
    async def admin_santa_start(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        config = await self.bot.config_service.get(interaction.guild.id)
        try:
            await self.bot.festival_service.require_staff(interaction.user, config)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = None
        if config.santa_channel_id:
            found = interaction.guild.get_channel(config.santa_channel_id)
            if found is not None and hasattr(found, "send"):
                channel = found
        if channel is None and interaction.channel is not None and hasattr(
            interaction.channel, "send"
        ):
            channel = interaction.channel
        if channel is None:
            await interaction.followup.send(
                embed=error_embed(
                    "Некуда отправить карточку",
                    "Выбери канал «Тайный Санта» в /z-admin config",
                ),
                ephemeral=True,
            )
            return
        try:
            event, previous = await self.bot.santa_service.create_event(interaction.guild.id)
            if previous is not None and not previous.drawn:
                previous.drawn = True
                await self.bot.db.mark_santa_drawn(previous.id)
                await refresh_santa_message(self.bot, previous)
            message = await publish_santa_message(
                self.bot,
                interaction.guild,
                event,
                channel,
                save=True,
            )
        except Exception:
            log.exception("Failed to create santa event")
            await interaction.followup.send(
                embed=error_embed("Не получилось создать тайного санту"),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed("Тайный Санта создан", f"[Открыть]({message.jump_url})"),
            ephemeral=True,
        )

    @admin.command(
        name="santa_pairs",
        description="Кто кому выпал",
    )
    @app_commands.guild_only()
    async def admin_santa_pairs(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        try:
            event = await self.bot.santa_service.require_active(interaction.guild.id)
            text = await self.bot.santa_service.format_pairs(interaction.guild, event)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=base_embed(
                title=f"Тайный Санта #{event.number}",
                description=text,
            ),
            ephemeral=True,
        )

    @admin.command(name="voice_listen", description="Бот заходит в твой войс и слушает речь")
    @app_commands.guild_only()
    async def admin_voice_listen(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)
        if not voice_listen_allowed(interaction.guild.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Войс → текст здесь выключен",
                    "Только на Ерундульках и разрешённом втором сервере.",
                ),
                ephemeral=True,
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed("Сначала зайди в голосовой канал"),
                ephemeral=True,
            )
            return
        if self.bot.ai_service.stt_credits_exhausted:
            await interaction.response.send_message(
                embed=error_embed(
                    "Токены исчерпаны",
                    "Лимит OpenRouter закончился — /z-admin voice_listen недоступен, "
                    "пока не пополнишь баланс и не перезапустишь бота.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        announce_id = interaction.channel_id
        try:
            text = await self.bot.voice_listen_service.join(
                self.bot,
                channel,
                announce_channel_id=announce_id,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        except Exception:
            log.exception("voice_listen join failed")
            await interaction.followup.send(
                embed=error_embed("Не получилось подключиться к войсу"),
                ephemeral=True,
            )
            return
        chat_note = (
            "Реплики в этот чат можно выключить в `/z-admin config` → флаги → «Войс → текст в чат»."
            if announce_id is not None and config.voice_chat_transcripts
            else (
                "Постинг в чат выключен флагом «Войс → текст в чат»."
                if announce_id is not None
                else "Реплики только в «дело» (сюда писать некуда)."
            )
        )
        await interaction.followup.send(
            embed=success_embed(
                "Слушаю войс",
                f"{text}\n{chat_note}\nКопятся в «дело» (~04:10).",
            ),
            ephemeral=True,
        )

    @admin.command(name="voice_leave", description="Бот выходит из войса и перестаёт слушать")
    @app_commands.guild_only()
    async def admin_voice_leave(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        text = await self.bot.voice_listen_service.leave(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(text),
            ephemeral=True,
        )

    @staticmethod
    def _format_log_clock(created_at: str, tz: ZoneInfo) -> str:
        raw = str(created_at or "").strip()
        if not raw:
            return "??:??:??"
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(tz).strftime("%H:%M:%S")
        except ValueError:
            return raw[:8] if len(raw) >= 8 else raw

    @admin.command(
        name="import",
        description="Выгрузить войсовый текст за сегодня в .txt (в ЛС)",
    )
    @app_commands.guild_only()
    async def admin_import(self, interaction: discord.Interaction) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        guild = interaction.guild
        if not voice_listen_allowed(guild.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Войс → текст здесь выключен",
                    "Импорт доступен только на Ерундульках и разрешённом втором сервере.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        config = await self.bot.config_service.get(guild.id)
        tz = ZoneInfo(config.timezone or "Europe/Moscow")
        day = datetime.now(tz).date().isoformat()
        rows = await self.bot.db.list_daily_text_logs_for_guild_day(
            guild.id,
            day,
            source="voice",
        )
        if not rows:
            await interaction.followup.send(
                embed=base_embed(
                    title="Импорт войса",
                    description=(
                        f"За сегодня (`{day}`) на этом сервере нет голосовых реплик.\n"
                        "Нужен `/z-admin voice_listen` и чтобы кто-то говорил."
                    ),
                ),
                ephemeral=True,
            )
            return

        member_names: dict[int, str] = {}
        skipped_left = 0
        for user_id in {row.user_id for row in rows}:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    skipped_left += 1
                    continue
            member_names[user_id] = member.display_name

        lines = [
            f"Сервер: {guild.name} ({guild.id})",
            f"День: {day} ({config.timezone or 'Europe/Moscow'})",
            "Источник: voice (только этот сервер)",
            "",
        ]
        kept = 0
        for row in rows:
            name = member_names.get(row.user_id)
            if name is None:
                continue
            clock = self._format_log_clock(row.created_at, tz)
            text = (row.text or "").replace("\r\n", "\n").replace("\r", "\n")
            for part in text.split("\n"):
                part = part.strip()
                if not part:
                    continue
                lines.append(f"[{clock}] {name}: {part}")
                kept += 1

        if kept == 0:
            await interaction.followup.send(
                embed=base_embed(
                    title="Импорт войса",
                    description=(
                        "Реплики за день есть в БД, но авторы уже не на этом сервере — "
                        "в файл ничего не попало."
                    ),
                ),
                ephemeral=True,
            )
            return

        payload = "\n".join(lines) + "\n"
        raw = payload.encode("utf-8-sig")
        # Stay under typical Discord attachment limits for safety.
        max_bytes = 7_500_000
        if len(raw) > max_bytes:
            raw = raw[:max_bytes] + "\n…(обрезано)\n".encode("utf-8")
        safe_guild = re.sub(r"[^\w\-]+", "_", guild.name, flags=re.UNICODE)[:40] or "guild"
        filename = f"voice_{safe_guild}_{day}.txt"
        note = f"Строк: **{kept}**."
        if skipped_left:
            note += f" Пропущено ушедших с сервера: **{skipped_left}**."
        try:
            await interaction.user.send(
                content=f"Войсовый текст за `{day}` — **{guild.name}**.\n{note}",
                file=discord.File(io.BytesIO(raw), filename=filename),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed(
                    "Не могу написать в ЛС",
                    "Открой личные сообщения от участников серверов и повтори.",
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            log.exception("Failed to DM voice import to user %s", interaction.user.id)
            await interaction.followup.send(
                embed=error_embed("Не удалось отправить файл в ЛС"),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embed=success_embed(f"Отправил `{filename}` тебе в ЛС. {note}"),
            ephemeral=True,
        )

    @admin.command(name="voice_dossier", description="Показать «дело» участника из войса")
    @app_commands.describe(user="Участник")
    @app_commands.guild_only()
    async def admin_voice_dossier(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        entries = await self.bot.db.list_voice_dossier(
            interaction.guild.id,
            user.id,
            limit=12,
        )
        swears = await self.bot.db.sum_voice_swears(interaction.guild.id, user.id)
        if not entries and not swears:
            await interaction.response.send_message(
                embed=base_embed(
                    title=f"Дело — {user.display_name}",
                    description=(
                        "Пока пусто. Включи `/z-admin voice_listen` и подожди "
                        "суточную обработку."
                    ),
                ),
                ephemeral=True,
            )
            return
        lines = [f"• {item.fact} _(до {item.source_date})_" for item in entries]
        if swears:
            lines.append(f"Маты (всего зафиксировано): **{swears}**")
        await interaction.response.send_message(
            embed=base_embed(
                title=f"Дело — {user.display_name}",
                description="\n".join(lines) if lines else "Пусто",
            ),
            ephemeral=True,
        )

    @admin.command(
        name="description",
        description="Справка по командам для участников",
    )
    @app_commands.describe(
        visibility="Кому отправить: только тебе или в канал",
    )
    @app_commands.choices(
        visibility=[
            app_commands.Choice(name="to_myself", value="to_myself"),
            app_commands.Choice(name="to_all", value="to_all"),
        ]
    )
    @app_commands.guild_only()
    async def admin_description(
        self,
        interaction: discord.Interaction,
        visibility: app_commands.Choice[str],
    ) -> None:
        denied = self._require_staff(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        config = await self.bot.config_service.get(interaction.guild.id)
        text = self.bot.config_service.build_public_help(config)
        ephemeral = visibility.value != "to_all"
        await interaction.response.send_message(content=text, ephemeral=ephemeral)

    # --- owner-only ---

    @test_admin.command(
        name="quote_who",
        description="Кто добавил цитату",
    )
    @app_commands.describe(quote_id="Номер цитаты")
    @app_commands.guild_only()
    async def test_quote_who(self, interaction: discord.Interaction, quote_id: int) -> None:
        denied = self._require_owner(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        quote = await self.bot.quote_service.get(interaction.guild.id, quote_id)
        if quote is None:
            await interaction.response.send_message(
                embed=error_embed("Цитата не найдена"),
                ephemeral=True,
            )
            return
        adder = interaction.guild.get_member(quote.added_by)
        if adder is not None:
            who = f"{adder.mention} (`{adder.id}`)"
        else:
            who = f"<@{quote.added_by}> (`{quote.added_by}`)"
        preview = self.bot.quote_service.preview_text(quote.content, 120)
        await interaction.response.send_message(
            embed=base_embed(
                title=f"Цитата #{quote.number}",
                description=f"Добавил: {who}\n«{preview}»",
            ),
            ephemeral=True,
        )

    @test_admin.command(
        name="voice",
        description="Сгенерировать TTS (HeyGen) и проиграть в войсе",
    )
    @app_commands.describe(
        voice="Голос HeyGen",
        text="Текст для озвучки",
    )
    @app_commands.choices(
        voice=[app_commands.Choice(name=name, value=name) for name in HEYGEN_VOICES]
    )
    @app_commands.guild_only()
    async def test_voice(
        self,
        interaction: discord.Interaction,
        voice: str,
        text: str,
    ) -> None:
        denied = self._require_owner(interaction)
        if denied:
            await interaction.response.send_message(
                embed=error_embed(denied), ephemeral=True
            )
            return
        assert interaction.guild is not None
        if not voice_listen_allowed(interaction.guild.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Войс → текст здесь выключен",
                    "Только на Ерундульках и разрешённом втором сервере.",
                ),
                ephemeral=True,
            )
            return
        cleaned = (text or "").strip()
        if not cleaned:
            await interaction.response.send_message(
                embed=error_embed("Нужен текст"),
                ephemeral=True,
            )
            return
        if not self.bot.voice_listen_service.is_listening(interaction.guild.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Бот не в войсе",
                    "Сначала `/z-admin voice_listen` в нужном канале.",
                ),
                ephemeral=True,
            )
            return
        if not self.bot.ai_service.tts_enabled:
            await interaction.response.send_message(
                embed=error_embed("Нужен OPENROUTER_API_KEY для TTS"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        voice_id = voice.casefold().strip()
        try:
            await self.bot.voice_listen_service.play_tts_text(
                interaction.guild.id,
                cleaned,
                voice=voice_id,
            )
        except OpenRouterCreditsExhausted:
            await interaction.followup.send(
                embed=error_embed("Токены OpenRouter исчерпаны (402)"),
                ephemeral=True,
            )
            return
        except OpenRouterRateLimited:
            await interaction.followup.send(
                embed=error_embed("OpenRouter TTS rate limit (429), попробуй чуть позже"),
                ephemeral=True,
            )
            return
        except ValueError as exc:
            await interaction.followup.send(
                embed=error_embed(str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            log.exception("test_admin voice TTS failed")
            await interaction.followup.send(
                embed=error_embed("Не удалось сгенерировать или проиграть голос"),
                ephemeral=True,
            )
            return
        tip = ""
        if self.bot.voice_listen_service.aisa_greeting_active():
            tip = (
                f"\nДо {AISA_GREETING_UNTIL.isoformat()} при входе @{AISA_USERNAME} "
                f"в канал бота играет «{AISA_GREETING_TEXT}» ({AISA_GREETING_VOICE})."
            )
        await interaction.followup.send(
            embed=success_embed(
                "Проиграно",
                f"Голос **{voice_id}**: {cleaned[:200]}"
                + ("…" if len(cleaned) > 200 else "")
                + tip,
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await self.bot.voice_listen_service.handle_aisa_voice_greeting(
            member, before, after
        )

    @staticmethod
    def _split_message(text: str, limit: int) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current = ""
        for block in text.split("\n\n"):
            piece = block if not current else f"{current}\n\n{block}"
            if len(piece) <= limit:
                current = piece
                continue
            if current:
                chunks.append(current)
            if len(block) <= limit:
                current = block
                continue
            for start in range(0, len(block), limit):
                chunks.append(block[start : start + limit])
            current = ""
        if current:
            chunks.append(current)
        return chunks or [text[:limit]]


async def setup(bot: ErundaBot) -> None:
    await bot.add_cog(AdminCog(bot))
