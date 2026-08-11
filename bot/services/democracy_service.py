"""Proposal / democracy business logic."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from bot.database.database import Database
from bot.database.models import GuildConfig, Proposal
from bot.utils.permissions import bot_can_manage_role

log = logging.getLogger(__name__)

ACTION_TYPES = frozenset({"create_role", "delete_role", "create_channel", "bot_config"})


class DemocracyService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        guild_id: int,
        author_id: int,
        content: str,
        tz_name: str,
        duration_hours: int | None = None,
        action_type: str | None = None,
        action_payload: dict | None = None,
    ) -> Proposal:
        config = await self.db.ensure_guild(guild_id)
        hours = duration_hours or config.proposal_duration_hours
        ends_at = datetime.now(ZoneInfo(tz_name)) + timedelta(hours=hours)
        if action_type and action_type not in ACTION_TYPES:
            raise ValueError(f"Неизвестное действие: {action_type}")
        number = await self.db.next_proposal_number(guild_id)
        payload_str = json.dumps(action_payload, ensure_ascii=False) if action_payload else None
        return await self.db.create_proposal(
            guild_id,
            number,
            content.strip(),
            author_id,
            ends_at.isoformat(),
            None,
            action_type,
            payload_str,
        )

    async def get(self, proposal_id: int) -> Proposal | None:
        return await self.db.get_proposal(proposal_id)

    async def cancel(self, proposal_id: int, user_id: int) -> Proposal:
        proposal = await self.db.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError("Предложение не найдено")
        if proposal.status != "open":
            raise ValueError("Голосование уже завершено")
        if proposal.author_id != user_id:
            raise ValueError("Отменить может только автор")
        return await self.db.update_proposal(proposal_id, status="cancelled")

    async def vote(self, proposal_id: int, user_id: int, yes: bool) -> Proposal:
        proposal = await self.db.get_proposal(proposal_id)
        if proposal is None:
            raise ValueError("Предложение не найдено")
        if proposal.status != "open":
            raise ValueError("Голосование завершено")
        await self.db.set_proposal_vote(proposal_id, user_id, "yes" if yes else "no")
        return proposal

    async def vote_counts(self, proposal_id: int) -> tuple[int, int]:
        return await self.db.count_proposal_votes(proposal_id)

    async def set_message(self, proposal_id: int, channel_id: int, message_id: int) -> Proposal:
        return await self.db.update_proposal(
            proposal_id, channel_id=channel_id, message_id=message_id
        )

    async def evaluate(self, proposal: Proposal, config: GuildConfig) -> str:
        """Return new status: passed or rejected."""
        yes, no = await self.vote_counts(proposal.id)
        total = yes + no
        if total < config.proposal_quorum:
            return "rejected"
        if total == 0:
            return "rejected"
        ratio = yes / total
        return "passed" if ratio >= config.proposal_pass_ratio else "rejected"

    async def close_proposal(
        self,
        proposal: Proposal,
        config: GuildConfig,
        bot: discord.Client,
    ) -> Proposal:
        if proposal.status != "open":
            return proposal
        status = await self.evaluate(proposal, config)
        updated = await self.db.update_proposal(proposal.id, status=status)
        if status == "passed" and config.auto_execute_proposals and proposal.action_type:
            guild = bot.get_guild(proposal.guild_id)
            if guild and guild.me:
                try:
                    await self.execute_action(guild, guild.me, updated)
                except Exception:
                    log.exception("Auto-execute failed for proposal %s", proposal.id)
        return updated

    async def execute_action(
        self,
        guild: discord.Guild,
        bot_member: discord.Member,
        proposal: Proposal,
    ) -> None:
        if not proposal.action_type or not proposal.action_payload:
            return
        try:
            payload = json.loads(proposal.action_payload)
        except json.JSONDecodeError:
            raise ValueError("Некорректный payload действия")

        if proposal.action_type == "create_role":
            if not bot_member.guild_permissions.manage_roles:
                raise ValueError("Нет права Manage Roles")
            name = payload.get("name", "Новая роль")
            await guild.create_role(name=str(name)[:100])
        elif proposal.action_type == "delete_role":
            if not bot_member.guild_permissions.manage_roles:
                raise ValueError("Нет права Manage Roles")
            role_id = int(payload["role_id"])
            role = guild.get_role(role_id)
            if role and bot_can_manage_role(bot_member, role):
                await role.delete()
        elif proposal.action_type == "create_channel":
            if not bot_member.guild_permissions.manage_channels:
                raise ValueError("Нет права Manage Channels")
            name = str(payload.get("name", "новый-канал"))[:100]
            await guild.create_text_channel(name)
        elif proposal.action_type == "bot_config":
            field = payload.get("field")
            value = payload.get("value")
            if field not in {"statistics_enabled", "personal_roles_enabled"}:
                raise ValueError("Недопустимое поле конфигурации")
            await self.db.update_guild(guild.id, **{field: int(bool(value))})

    def time_left_label(self, proposal: Proposal, tz_name: str) -> str:
        ends = datetime.fromisoformat(proposal.ends_at)
        now = datetime.now(ZoneInfo(tz_name))
        if ends <= now:
            return "завершено"
        delta = ends - now
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes = rem // 60
        if hours:
            return f"{hours} ч {minutes} мин"
        return f"{minutes} мин"
