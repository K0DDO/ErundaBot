"""Per-user tarot decks: draw without repeats until reshuffle."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.database.database import Database
from bot.services.tarot_cards import TAROT_DECK, TarotCard

TAROT_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "tarot"


class TarotDeckEmpty(Exception):
    def __init__(self, minutes_left: int) -> None:
        super().__init__("Карты закончились")
        self.minutes_left = minutes_left


def _parse(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


class TarotService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._rng = random.SystemRandom()

    @staticmethod
    def image_path(card: TarotCard, reversed_: bool) -> Path:
        return TAROT_ASSETS_DIR / card.image_name(reversed_)

    async def draw(
        self,
        guild_id: int,
        user_id: int,
        reset_minutes: int,
    ) -> tuple[TarotCard, bool]:
        now = datetime.now(timezone.utc)
        reset = timedelta(minutes=max(1, reset_minutes))
        draws = await self.db.list_tarot_draws(guild_id, user_id)
        if draws:
            last = max(_parse(drawn_at) for _card_id, drawn_at in draws)
            if now - last >= reset:
                await self.db.clear_tarot_draws(guild_id, user_id)
                draws = []
            else:
                drawn_ids = {card_id for card_id, _drawn_at in draws}
                if len(drawn_ids) >= len(TAROT_DECK):
                    left = (last + reset - now).total_seconds()
                    raise TarotDeckEmpty(max(1, math.ceil(left / 60)))
        drawn_ids = {card_id for card_id, _drawn_at in draws}
        remaining = [card for card in TAROT_DECK if card.id not in drawn_ids]
        card = self._rng.choice(remaining)
        reversed_ = self._rng.random() < 0.5
        await self.db.add_tarot_draw(guild_id, user_id, card.id, reversed_, now.isoformat())
        return card, reversed_

    async def shuffle(self, guild_id: int, user_id: int) -> None:
        await self.db.clear_tarot_draws(guild_id, user_id)
