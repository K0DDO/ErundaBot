"""Admin / staff slash commands under /админ (sorts after Latin names)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.models import Birthday
from bot.services.festival_service import normalize_film_title
from bot.services.santa_service import has_kino_role
from bot.utils.embeds import base_embed, error_embed, success_embed
from bot.utils.permissions import (
    can_edit_config,
    can_use_tgk_debug,
    config_denied_reason,
    find_member_by_nickname_async,
    tgk_debug_denied_reason,
)
from bot.views.config_views import ConfigPanel, config_overview_embed
from bot.views.santa_views import publish_santa_message, refresh_santa_message
from bot.views.tgk_views import refresh_tgk_board

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)


class AdminCog(commands.Cog):
    def __init__(self, bot: ErundaBot) -> None:
        self.bot = bot

    admin = app_commands.Group(
        name="админ",
        description="Админские и служебные команды Ерунды",
    )

    @admin.command(name="config", description="Настройки Ерунды для этого сервера")
    @app_commands.guild_only()
    async def admin_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=error_embed("Только на сервере"),
                ephemeral=True,
            )
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Недостаточно прав",
                    config_denied_reason(config.config_role_id),
                ),
                ephemeral=True,
            )
            return
        view = ConfigPanel(self.bot, interaction.guild.id)
        await interaction.response.send_message(
            embed=config_overview_embed(config),
            view=view,
            ephemeral=True,
        )

    @admin.command(name="birthday_test", description="Тестовое поздравление (видно только тебе)")
    @app_commands.describe(user="Кого поздравить, по умолчанию тебя")
    @app_commands.guild_only()
    async def admin_birthday_test(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(config_denied_reason(config.config_role_id)),
                ephemeral=True,
            )
            return
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
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(config_denied_reason(config.config_role_id)),
                ephemeral=True,
            )
            return
        if not config.quotes_channel_id:
            await interaction.response.send_message(
                embed=error_embed("Канал цитат не настроен в /админ config"),
                ephemeral=True,
            )
            return
        channel = interaction.guild.get_channel(config.quotes_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("Канал цитат недоступен"),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        removed = await self.bot.quote_service.cleanup_channel(interaction.guild, channel)
        numbered = await self.bot.quote_service.renumber_and_sync(interaction.guild)
        await interaction.followup.send(
            embed=success_embed(
                "Очистка завершена",
                f"Удалено сообщений: **{removed}** в {channel.mention}\n"
                f"Номера переназначены: **{numbered}**, старше — меньше номер.",
            ),
        )

    @admin.command(name="quote_who", description="Кто добавил цитату (только роль Кино)")
    @app_commands.describe(quote_id="Номер цитаты")
    @app_commands.guild_only()
    async def admin_quote_who(self, interaction: discord.Interaction, quote_id: int) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not has_kino_role(interaction.user, config):
            await interaction.response.send_message(
                embed=error_embed("Нужна роль «Кино»"),
                ephemeral=True,
            )
            return
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

    @admin.command(name="fest_stats", description="Оценки и присутствие на сеансе")
    @app_commands.describe(number="Номер кинофестиваля")
    @app_commands.guild_only()
    async def admin_fest_stats(self, interaction: discord.Interaction, number: int) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
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

    @admin.command(name="tgk_debug_add", description="Добавить ТГК участнику по нику (отладка)")
    @app_commands.describe(nickname="Ник на сервере", link="Ссылка на канал")
    @app_commands.guild_only()
    async def admin_tgk_debug_add(
        self,
        interaction: discord.Interaction,
        nickname: str,
        link: str,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_use_tgk_debug(interaction.user, config.tgk_list_role_id):
            await interaction.response.send_message(
                embed=error_embed(
                    "Недостаточно прав",
                    tgk_debug_denied_reason(config.tgk_list_role_id),
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            target = await find_member_by_nickname_async(interaction.guild, nickname)
            channel = await self.bot.tgk_service.add(
                interaction.guild.id,
                target.id,
                link,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await refresh_tgk_board(self.bot, interaction.guild)
        parts = [f"**{channel.title}**", f"Владелец: {target.display_name}"]
        if channel.image_url:
            parts.append("Картинка подтянулась.")
        else:
            parts.append("Название взял с t.me, картинку получить не удалось.")
        await interaction.followup.send(
            embed=success_embed(f"ТГК #{channel.number} добавлен", "\n".join(parts)),
            ephemeral=True,
        )

    @admin.command(name="santa_start", description="Новый тайный Санта — пустая карточка в канал")
    @app_commands.guild_only()
    async def admin_santa_start(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
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
                    "Выбери канал «Тайный Санта» в /админ config",
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

    @admin.command(name="voice_listen", description="Бот заходит в твой войс и слушает речь")
    @app_commands.guild_only()
    async def admin_voice_listen(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(config_denied_reason(config.config_role_id)),
                ephemeral=True,
            )
            return
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
                    "Лимит OpenRouter закончился — `/админ voice_listen` недоступен, "
                    "пока не пополнишь баланс и не перезапустишь бота.",
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        announce = None
        if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            announce = interaction.channel
        try:
            text = await self.bot.voice_listen_service.join(
                self.bot,
                channel,
                announce_channel=announce,
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
            "Реплики в этот чат можно выключить в `/админ config` → флаги → «Войс → текст в чат»."
            if announce is not None and config.voice_chat_transcripts
            else (
                "Постинг в чат выключен флагом «Войс → текст в чат»."
                if announce is not None
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
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(config_denied_reason(config.config_role_id)),
                ephemeral=True,
            )
            return
        text = await self.bot.voice_listen_service.leave(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(text),
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
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not can_edit_config(interaction.user, config.config_role_id):
            await interaction.response.send_message(
                embed=error_embed(config_denied_reason(config.config_role_id)),
                ephemeral=True,
            )
            return
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
                    description="Пока пусто. Включи `/админ voice_listen` и подожди суточную обработку.",
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

    @admin.command(name="santa_pairs", description="Кто кому выпал (только роль Кино)")
    @app_commands.guild_only()
    async def admin_santa_pairs(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        config = await self.bot.config_service.get(interaction.guild.id)
        if not has_kino_role(interaction.user, config):
            await interaction.response.send_message(
                embed=error_embed("Нужна роль «Кино»"),
                ephemeral=True,
            )
            return
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
