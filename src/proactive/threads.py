# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Inter-agent thread lifecycle (Enhancement 015 / v0.16.3).

A thread is a bounded bot-to-bot conversation, started by one persona's
`engage_persona` action and observed message-by-message via on_message hooks.
At most one thread is active per channel — starting a new one supersedes any
existing.

Termination conditions (all log an `ended_reason`):
- `turn_cap`         — turn_count >= max_turns
- `human_interrupt`  — any non-bot message in the channel
- `natural_end`      — same persona's last 2 non-prefilter decisions in this
                       thread are both 'none'
- `budget_exhausted` — initiator's daily budget hit 0
- `superseded`       — another thread started in this channel
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import asyncpg

logger = logging.getLogger("slashAI.proactive.threads")

DEFAULT_MAX_TURNS = 4


@dataclass
class ThreadState:
    """Active or recently-ended inter-agent thread row."""
    id: int
    channel_id: int
    guild_id: Optional[int]
    initiator_persona_id: str
    participants: list[dict[str, Any]]   # [{"persona_id": "...", "user_id": 123}, ...]
    turn_count: int
    max_turns: int
    seed_message_id: Optional[int]
    seed_topic: Optional[str]
    started_at: datetime
    last_turn_at: datetime
    ended_at: Optional[datetime] = None
    ended_reason: Optional[str] = None

    def participant_user_ids(self) -> set[int]:
        return {int(p["user_id"]) for p in self.participants if "user_id" in p}

    def participant_persona_ids(self) -> list[str]:
        return [p["persona_id"] for p in self.participants if "persona_id" in p]

    def other_participant(self, self_persona_id: str) -> Optional[str]:
        for p in self.participants:
            if p.get("persona_id") != self_persona_id:
                return p.get("persona_id")
        return None


def engagement_decay_factor(turn_count: int) -> float:
    """At turn 0, no decay. At turn 4, ~20% probability.

    Soft hint passed to the decider, not a math knob it must compute.
    """
    return max(0.2, 1.0 - 0.2 * turn_count)


class InterAgentThreads:
    """Async wrapper around `inter_agent_threads` lifecycle."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_thread(
        self,
        *,
        initiator_persona_id: str,
        channel_id: int,
        guild_id: Optional[int],
        participants: list[dict[str, Any]],
        seed_message_id: Optional[int] = None,
        seed_topic: Optional[str] = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> ThreadState:
        """Start a new thread, superseding any active thread in the same channel.

        Returns the new ThreadState with turn_count=0; advance_thread is called
        explicitly by the actor after the seed message is posted.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Supersede any existing active thread
                await conn.execute(
                    """
                    UPDATE inter_agent_threads
                    SET ended_at = NOW(),
                        ended_reason = 'superseded'
                    WHERE channel_id = $1 AND ended_at IS NULL
                    """,
                    channel_id,
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO inter_agent_threads
                        (channel_id, guild_id, initiator_persona_id, participants,
                         turn_count, max_turns, seed_message_id, seed_topic)
                    VALUES ($1, $2, $3, $4::jsonb, 0, $5, $6, $7)
                    RETURNING id, channel_id, guild_id, initiator_persona_id,
                              participants, turn_count, max_turns,
                              seed_message_id, seed_topic,
                              started_at, last_turn_at, ended_at, ended_reason
                    """,
                    channel_id,
                    guild_id,
                    initiator_persona_id,
                    json.dumps(participants),
                    max_turns,
                    seed_message_id,
                    seed_topic,
                )
        return self._row_to_state(row)

    async def advance_thread(self, thread_id: int) -> Optional[ThreadState]:
        """Increment turn_count and update last_turn_at. No-op if already ended.

        Returns the post-update state, or None if the thread doesn't exist /
        is already ended.
        """
        row = await self.db.fetchrow(
            """
            UPDATE inter_agent_threads
            SET turn_count = turn_count + 1, last_turn_at = NOW()
            WHERE id = $1 AND ended_at IS NULL
            RETURNING id, channel_id, guild_id, initiator_persona_id,
                      participants, turn_count, max_turns,
                      seed_message_id, seed_topic,
                      started_at, last_turn_at, ended_at, ended_reason
            """,
            thread_id,
        )
        if row is None:
            return None
        return self._row_to_state(row)

    async def end_thread(self, thread_id: int, reason: str) -> None:
        """Mark a thread as ended. Idempotent — no-ops if already ended."""
        await self.db.execute(
            """
            UPDATE inter_agent_threads
            SET ended_at = NOW(), ended_reason = $2
            WHERE id = $1 AND ended_at IS NULL
            """,
            thread_id,
            reason,
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_active_thread(self, channel_id: int) -> Optional[ThreadState]:
        row = await self.db.fetchrow(
            """
            SELECT id, channel_id, guild_id, initiator_persona_id,
                   participants, turn_count, max_turns,
                   seed_message_id, seed_topic,
                   started_at, last_turn_at, ended_at, ended_reason
            FROM inter_agent_threads
            WHERE channel_id = $1 AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            channel_id,
        )
        if row is None:
            return None
        return self._row_to_state(row)

    async def list_recent(self, limit: int = 25) -> list[ThreadState]:
        rows = await self.db.fetch(
            """
            SELECT id, channel_id, guild_id, initiator_persona_id,
                   participants, turn_count, max_turns,
                   seed_message_id, seed_topic,
                   started_at, last_turn_at, ended_at, ended_reason
            FROM inter_agent_threads
            ORDER BY started_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [self._row_to_state(r) for r in rows]

    # ------------------------------------------------------------------
    # Termination helpers (called from on_message)
    # ------------------------------------------------------------------

    async def end_active_if_human(self, channel_id: int) -> Optional[int]:
        """If a thread is active in this channel, end it with reason='human_interrupt'.

        Returns the ended thread's id, or None if no thread was active.
        """
        active = await self.get_active_thread(channel_id)
        if active is None:
            return None
        await self.end_thread(active.id, "human_interrupt")
        logger.info(
            f"[threads] human_interrupt ended thread {active.id} "
            f"in channel {channel_id} (turn_count={active.turn_count})"
        )
        return active.id

    async def end_if_turn_cap(self, thread: ThreadState) -> bool:
        """Check turn cap; end if hit. Returns True if ended."""
        if thread.turn_count >= thread.max_turns:
            await self.end_thread(thread.id, "turn_cap")
            logger.info(
                f"[threads] turn_cap ended thread {thread.id} "
                f"({thread.turn_count}/{thread.max_turns})"
            )
            return True
        return False

    async def check_natural_end(
        self,
        thread_id: int,
        persona_id: str,
    ) -> bool:
        """Returns True iff the persona's last 2 non-prefilter decisions in this
        thread are both 'none' (signals "winding down")."""
        rows = await self.db.fetch(
            """
            SELECT decision, reasoning
            FROM proactive_actions
            WHERE inter_agent_thread_id = $1
              AND persona_id = $2
              AND (reasoning IS NULL OR reasoning NOT LIKE 'prefilter:%')
            ORDER BY created_at DESC
            LIMIT 2
            """,
            thread_id,
            persona_id,
        )
        if len(rows) < 2:
            return False
        return all(r["decision"] == "none" for r in rows)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_state(row) -> ThreadState:
        participants = row["participants"]
        # asyncpg may return JSONB as str or list depending on codec setup
        if isinstance(participants, str):
            participants = json.loads(participants)
        return ThreadState(
            id=int(row["id"]),
            channel_id=int(row["channel_id"]),
            guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
            initiator_persona_id=row["initiator_persona_id"],
            participants=list(participants or []),
            turn_count=int(row["turn_count"]),
            max_turns=int(row["max_turns"]),
            seed_message_id=int(row["seed_message_id"]) if row["seed_message_id"] is not None else None,
            seed_topic=row["seed_topic"],
            started_at=row["started_at"],
            last_turn_at=row["last_turn_at"],
            ended_at=row["ended_at"],
            ended_reason=row["ended_reason"],
        )
