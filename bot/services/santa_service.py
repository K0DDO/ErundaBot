"""Secret Santa: signup card, derangement draw, DMs."""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import discord

from bot.database.database import Database
from bot.database.models import GuildConfig, SantaEntry, SantaEvent
from bot.utils.permissions import can_staff

if TYPE_CHECKING:
    from bot.bot import ErundaBot

log = logging.getLogger(__name__)

SANTA_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "santa"
SANTA_CARD_IMAGE = SANTA_ASSETS_DIR / "card.jpg"
SANTA_CARD_FILENAME = "card.jpg"
SANTA_COLOR = 0xC41E3A
URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def member_server_nick(member: discord.Member) -> str:
    if member.nick:
        return member.nick
    return member.global_name or member.name


def has_kino_role(member: discord.Member, config: GuildConfig) -> bool:
    # Legacy name — staff is owner or guild admin (Кино role removed).
    _ = config
    return can_staff(member)


def can_santa_draw(member: discord.Member, config: GuildConfig) -> bool:
    _ = config
    return can_staff(member)


def normalize_wishlist_url(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("Укажи ссылку на вишлист")
    if not URL_RE.match(text):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Вишлист должен быть ссылкой (https://…)")
    return text


def derange(user_ids: list[int]) -> list[tuple[int, int]]:
    """Return giver→receiver pairs with no fixed points."""
    if len(user_ids) < 2:
        raise ValueError("Нужно минимум 2 участника")
    givers = list(user_ids)
    receivers = list(user_ids)
    rng = random.SystemRandom()
    for _ in range(200):
        rng.shuffle(receivers)
        if all(a != b for a, b in zip(givers, receivers, strict=True)):
            return list(zip(givers, receivers, strict=True))
    # Guaranteed derangement via cyclic shift of a shuffle.
    rng.shuffle(receivers)
    if len(receivers) == 2 and receivers[0] == givers[0]:
        receivers = list(reversed(receivers))
    else:
        receivers = receivers[1:] + receivers[:1]
        if any(a == b for a, b in zip(givers, receivers, strict=True)):
            receivers = receivers[1:] + receivers[:1]
    if any(a == b for a, b in zip(givers, receivers, strict=True)):
        raise ValueError("Не удалось перемешать без совпадений — попробуй ещё раз")
    return list(zip(givers, receivers, strict=True))


class SantaService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def card_file(self) -> discord.File:
        if not SANTA_CARD_IMAGE.is_file():
            raise FileNotFoundError(f"Нет картинки санты: {SANTA_CARD_IMAGE}")
        return discord.File(SANTA_CARD_IMAGE, filename=SANTA_CARD_FILENAME)

    async def require_active(self, guild_id: int) -> SantaEvent:
        event = await self.db.get_active_santa_event(guild_id)
        if event is None:
            raise ValueError("Нет активного тайного санты — сначала /.admin santa_start")
        return event

    async def create_event(self, guild_id: int) -> tuple[SantaEvent, SantaEvent | None]:
        previous = await self.db.get_active_santa_event(guild_id)
        event = await self.db.create_santa_event(guild_id)
        return event, previous

    async def upsert_entry(
        self,
        event_id: int,
        user_id: int,
        address: str,
        wishlist_raw: str,
    ) -> SantaEntry:
        event = await self.db.get_santa_event(event_id)
        if event is None:
            raise ValueError("Розыгрыш не найден")
        if event.drawn:
            raise ValueError("Розыгрыш уже прошёл — менять заявку нельзя")
        address_clean = address.strip()
        if len(address_clean) < 3:
            raise ValueError("Укажи адрес или ориентир с ПВЗ")
        wishlist = normalize_wishlist_url(wishlist_raw)
        return await self.db.upsert_santa_entry(event_id, user_id, address_clean, wishlist)

    async def leave(self, event_id: int, user_id: int) -> None:
        event = await self.db.get_santa_event(event_id)
        if event is None:
            raise ValueError("Розыгрыш не найден")
        if event.drawn:
            raise ValueError("Розыгрыш уже прошёл — уйти нельзя")
        removed = await self.db.delete_santa_entry(event_id, user_id)
        if not removed:
            raise ValueError("Ты не в списке участников")

    async def participant_lines(
        self,
        guild: discord.Guild,
        event_id: int,
    ) -> list[str]:
        entries = await self.db.list_santa_entries(event_id)
        lines: list[str] = []
        for entry in entries:
            member = guild.get_member(entry.user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(entry.user_id)
                except discord.HTTPException:
                    lines.append(f"• пользователь `{entry.user_id}`")
                    continue
            lines.append(f"• {member_server_nick(member)}")
        return lines

    async def draw_and_notify(
        self,
        bot: ErundaBot,
        guild: discord.Guild,
        event: SantaEvent,
    ) -> tuple[list[str], list[str]]:
        if event.drawn:
            raise ValueError("Розыгрыш уже был — повтор запрещён")
        entries = await self.db.list_santa_entries(event.id)
        if len(entries) < 2:
            raise ValueError("Нужно минимум 2 участника")
        by_id = {entry.user_id: entry for entry in entries}
        pairs = derange([entry.user_id for entry in entries])
        await self.db.save_santa_assignments(event.id, pairs)
        await self.db.mark_santa_drawn(event.id)

        ok: list[str] = []
        failed: list[str] = []
        for giver_id, receiver_id in pairs:
            giver = guild.get_member(giver_id)
            receiver = guild.get_member(receiver_id)
            receiver_entry = by_id[receiver_id]
            giver_label = member_server_nick(giver) if giver else f"`{giver_id}`"
            receiver_label = (
                member_server_nick(receiver) if receiver else f"`{receiver_id}`"
            )
            if giver is None:
                failed.append(f"{giver_label} → {receiver_label} (нет на сервере)")
                continue
            embed = discord.Embed(
                title="🎅 Тайный Санта",
                description=(
                    f"Ты даришь подарок **{receiver_label}**.\n\n"
                    f"**Адрес / ПВЗ:**\n{receiver_entry.address}\n\n"
                    f"**Вишлист:**\n{receiver_entry.wishlist_url}"
                ),
                color=SANTA_COLOR,
            )
            try:
                await giver.send(embed=embed)
                ok.append(f"{giver_label} → {receiver_label}")
            except discord.HTTPException:
                failed.append(f"{giver_label} → {receiver_label} (ЛС закрыты)")
                log.warning("Santa DM failed for %s in guild %s", giver_id, guild.id)
        return ok, failed

    async def format_pairs(
        self,
        guild: discord.Guild,
        event: SantaEvent,
    ) -> str:
        assignments = await self.db.list_santa_assignments(event.id)
        if not assignments:
            return "Пар ещё нет — сначала `/santa start`."
        lines: list[str] = []
        for item in assignments:
            giver = guild.get_member(item.giver_id)
            receiver = guild.get_member(item.receiver_id)
            g = member_server_nick(giver) if giver else f"`{item.giver_id}`"
            r = member_server_nick(receiver) if receiver else f"`{item.receiver_id}`"
            lines.append(f"**{g}** → **{r}**")
        return "\n".join(lines)
