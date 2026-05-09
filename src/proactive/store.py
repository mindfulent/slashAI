# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
SQL helpers for the proactive subsystem.

All queries operate on `proactive_actions` (migration 018a) and
`inter_agent_threads` (018b). Daily-budget computation is centralized
here so the policy layer remains pure-Python.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import asyncpg

logger = logging.getLogger("slashAI.proactive.store")


@dataclass
class BudgetSummary:
    reactions: int
    replies: int
    new_topics: int


@dataclass
class ActionRecord:
    persona_id: str
    channel_id: int
    guild_id: Optional[int]
    decision: str                       # 'none' | 'react' | 'reply' | 'new_topic' | 'engage_persona'
    trigger: str                        # 'activity' | 'heartbeat'
    target_message_id: Optional[int] = None
    target_persona_id: Optional[str] = None
    emoji: Optional[str] = None
    posted_message_id: Optional[int] = None
    inter_agent_thread_id: Optional[int] = None
    reasoning: Optional[str] = None
    confidence: Optional[float] = None
    decider_model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class ProactiveStore:
    """Thin async wrapper over `proactive_actions` queries."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool

    async def record_action(self, row: ActionRecord) -> int:
        """Insert one decision row. Returns the new id."""
        new_id = await self.db.fetchval(
            """
            INSERT INTO proactive_actions
                (persona_id, channel_id, guild_id, decision, trigger,
                 target_message_id, target_persona_id, emoji, posted_message_id,
                 inter_agent_thread_id, reasoning, confidence, decider_model,
                 input_tokens, output_tokens)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING id
            """,
            row.persona_id,
            row.channel_id,
            row.guild_id,
            row.decision,
            row.trigger,
            row.target_message_id,
            row.target_persona_id,
            row.emoji,
            row.posted_message_id,
            row.inter_agent_thread_id,
            row.reasoning,
            row.confidence,
            row.decider_model,
            row.input_tokens,
            row.output_tokens,
        )
        return int(new_id)

    async def daily_budget_used(self, persona_id: str, since: datetime) -> dict[str, int]:
        """Return {decision: count} for non-noop actions since `since`."""
        rows = await self.db.fetch(
            """
            SELECT decision, COUNT(*)::INT AS n
            FROM proactive_actions
            WHERE persona_id = $1
              AND created_at >= $2
              AND decision != 'none'
            GROUP BY decision
            """,
            persona_id,
            since,
        )
        return {r["decision"]: int(r["n"]) for r in rows}

    async def last_action_in_channel(
        self, channel_id: int, exclude_persona: Optional[str] = None
    ) -> Optional[datetime]:
        """Most recent non-noop action timestamp in a channel, by any persona.

        If `exclude_persona` is given, ignore that persona's own actions
        (used to compute cross-persona lockout for the calling persona).
        """
        if exclude_persona is None:
            return await self.db.fetchval(
                """
                SELECT MAX(created_at) FROM proactive_actions
                WHERE channel_id = $1 AND decision != 'none'
                """,
                channel_id,
            )
        return await self.db.fetchval(
            """
            SELECT MAX(created_at) FROM proactive_actions
            WHERE channel_id = $1
              AND persona_id != $2
              AND decision != 'none'
            """,
            channel_id,
            exclude_persona,
        )

    async def last_persona_action_in_channel(
        self, persona_id: str, channel_id: int
    ) -> Optional[datetime]:
        """Most recent non-noop action timestamp for this persona in this channel."""
        return await self.db.fetchval(
            """
            SELECT MAX(created_at) FROM proactive_actions
            WHERE persona_id = $1 AND channel_id = $2 AND decision != 'none'
            """,
            persona_id,
            channel_id,
        )

    async def recent_history(
        self,
        persona_id: Optional[str] = None,
        channel_id: Optional[int] = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Most recent decisions for /proactive history."""
        conds = []
        params: list[Any] = []
        if persona_id:
            params.append(persona_id)
            conds.append(f"persona_id = ${len(params)}")
        if channel_id:
            params.append(channel_id)
            conds.append(f"channel_id = ${len(params)}")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        params.append(limit)
        rows = await self.db.fetch(
            f"""
            SELECT id, persona_id, channel_id, decision, trigger, emoji,
                   target_message_id, target_persona_id, posted_message_id,
                   reasoning, confidence, decider_model,
                   input_tokens, output_tokens, created_at
            FROM proactive_actions
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        return [dict(r) for r in rows]
