# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Sycophancy detection (Enhancement 015 / v0.16.5 polish).

Cemri et al. 2025's MAST taxonomy doesn't list sycophancy explicitly, but
adjacent literature reports ~58% baseline agreement in single-LLM
interactions. This module ships a heuristic agreement-language scan over
recent inter-agent threads. It's *not* a model-grade detector — it's a
cheap signal that flags persona pairs whose threads are reading as a
mutual-validation loop.

Detection model (deliberately simple):
- Scan recent `proactive_actions` rows where `decision = 'reply'` AND
  `inter_agent_thread_id IS NOT NULL`.
- For each row, the `reasoning` field (decider's stated 'why') is the
  signal. We don't have access to the posted message body without a Discord
  fetch, but the reasoning often paraphrases the action ("agreeing with
  Lena's farming take", "echoing slashAI's redstone advice").
- Count agreement-cue hits per persona. Surface raw counts and per-thread
  cue density to the operator.

A real sycophancy detector would compare consecutive turn embeddings for
similarity drift, score insight novelty across a thread, and look at how
often a persona's reasoning references the *other* persona's prior turn.
That's future work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import asyncpg

logger = logging.getLogger("slashAI.proactive.sycophancy")


# Word-boundary patterns for common agreement / validation cues. Lowercase
# matching only; we feed the regex `re.IGNORECASE`.
_AGREEMENT_CUES: tuple[str, ...] = (
    r"\bagree\b",
    r"\bagreement\b",
    r"\bagreeing\b",
    r"\bechoing\b",
    r"\bechoes\b",
    r"\bvalidat",          # validate, validation, validating
    r"\baffirm",           # affirm, affirms, affirming
    r"\bconcur",           # concur, concurring
    r"\bsupport(?:ing)?\b",
    r"\bback\s+up\b",
    r"\bbacking\s+up\b",
    r"\b(?:totally|completely|absolutely)\s+(?:right|true|correct)\b",
    r"\bspot\s*on\b",
    r"\bgood\s+point\b",
    r"\bfair\s+point\b",
    r"\bnice\s+take\b",
    r"\bwell\s+put\b",
    r"\b\+1\b",
    r"\bsame\b",
    r"\bditto\b",
    r"\bexactly\b",
    r"\byeah\b",
    r"\byep\b",
    r"\byup\b",
)

_AGREEMENT_RE = re.compile("|".join(_AGREEMENT_CUES), re.IGNORECASE)


@dataclass
class PersonaSycophancyStats:
    persona_id: str
    reply_count: int
    agreement_hits: int
    threads_seen: int

    @property
    def agreement_rate(self) -> float:
        if self.reply_count == 0:
            return 0.0
        return self.agreement_hits / self.reply_count


@dataclass
class ThreadSycophancyStats:
    thread_id: int
    initiator_persona_id: str
    participants: str           # "slashai ↔ lena"
    turn_count: int
    total_replies_in_thread: int
    agreement_hits: int
    ended_reason: Optional[str]


def count_agreement_cues(text: Optional[str]) -> int:
    """Number of agreement-language hits in `text`. Case-insensitive."""
    if not text:
        return 0
    return len(_AGREEMENT_RE.findall(text))


class SycophancyDetector:
    """Heuristic agreement-language aggregator over inter-agent threads."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool

    async def per_persona(self, days: int = 7) -> list[PersonaSycophancyStats]:
        """Aggregate per-persona stats over the lookback window.

        Counts replies inside inter_agent_threads (NOT mention/DM replies —
        only proactive replies that happened during a bot-to-bot thread).
        """
        rows = await self.db.fetch(
            """
            SELECT pa.persona_id,
                   pa.reasoning,
                   pa.inter_agent_thread_id
            FROM proactive_actions pa
            WHERE pa.decision = 'reply'
              AND pa.inter_agent_thread_id IS NOT NULL
              AND pa.created_at >= NOW() - make_interval(days => $1)
            """,
            days,
        )

        per_persona: dict[str, dict[str, int]] = {}
        threads_per_persona: dict[str, set[int]] = {}
        for r in rows:
            pid = r["persona_id"]
            stats = per_persona.setdefault(pid, {"reply_count": 0, "agreement_hits": 0})
            stats["reply_count"] += 1
            stats["agreement_hits"] += count_agreement_cues(r["reasoning"])
            tid = r["inter_agent_thread_id"]
            if tid is not None:
                threads_per_persona.setdefault(pid, set()).add(int(tid))

        out = []
        for pid, s in sorted(per_persona.items(), key=lambda x: x[0]):
            out.append(
                PersonaSycophancyStats(
                    persona_id=pid,
                    reply_count=s["reply_count"],
                    agreement_hits=s["agreement_hits"],
                    threads_seen=len(threads_per_persona.get(pid, set())),
                )
            )
        return out

    async def per_thread(self, days: int = 7, limit: int = 20) -> list[ThreadSycophancyStats]:
        """Aggregate per-thread stats — useful for "show me the worst offenders"."""
        rows = await self.db.fetch(
            """
            SELECT t.id AS thread_id,
                   t.initiator_persona_id,
                   t.participants,
                   t.turn_count,
                   t.ended_reason,
                   COALESCE(SUM(CASE WHEN pa.decision = 'reply' THEN 1 ELSE 0 END), 0)::INT
                       AS reply_count,
                   COALESCE(STRING_AGG(pa.reasoning, ' | '), '')
                       AS combined_reasoning
            FROM inter_agent_threads t
            LEFT JOIN proactive_actions pa ON pa.inter_agent_thread_id = t.id
            WHERE t.started_at >= NOW() - make_interval(days => $1)
            GROUP BY t.id
            ORDER BY t.started_at DESC
            LIMIT $2
            """,
            days,
            limit,
        )

        out: list[ThreadSycophancyStats] = []
        for r in rows:
            participants = r["participants"]
            # asyncpg may return JSONB as str or list
            if isinstance(participants, str):
                import json as _json
                try:
                    participants = _json.loads(participants)
                except Exception:
                    participants = []
            if isinstance(participants, list):
                names = [p.get("persona_id", "?") for p in participants if isinstance(p, dict)]
                pretty = " ↔ ".join(names) or "?"
            else:
                pretty = "?"

            out.append(
                ThreadSycophancyStats(
                    thread_id=int(r["thread_id"]),
                    initiator_persona_id=r["initiator_persona_id"],
                    participants=pretty,
                    turn_count=int(r["turn_count"]),
                    total_replies_in_thread=int(r["reply_count"]),
                    agreement_hits=count_agreement_cues(r["combined_reasoning"]),
                    ended_reason=r["ended_reason"],
                )
            )
        return out
