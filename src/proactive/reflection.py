# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Park-style reflection engine (Enhancement 015 / v0.16.4).

Adopts Generative Agents (Park et al. 2023) prompts nearly verbatim:
- Importance scoring (1-10 poignancy rating per observation)
- Threshold-based reflection trigger (sum > 150 since last reflection)
- Salient-questions then synthesis (5 insights with citation provenance)

Reflections feed back into the decider's context bundle. When slashAI
considers replying to a message that mentions Lena, it retrieves prior
reflections about Lena.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

logger = logging.getLogger("slashAI.proactive.reflection")


PARK_IMPORTANCE_PROMPT = (
    "On the scale of 1 to 10, where 1 is purely mundane (e.g., brushing teeth, "
    "making bed) and 10 is extremely poignant (e.g., a break up, college "
    "acceptance), rate the likely poignancy of the following piece of memory.\n"
    "Memory: {memory}\n"
    "Rating:"
)

PARK_SALIENT_QUESTIONS_PROMPT = (
    "{statements}\n\n"
    "Given only the information above, what are 3 most salient high-level "
    "questions we can answer about the subjects in the statements? "
    "Reply with one question per line, numbered 1, 2, 3."
)

PARK_SYNTHESIS_PROMPT = (
    "Statements about {subject}:\n"
    "{numbered_memories}\n\n"
    "What 5 high-level insights can you infer from the above statements? "
    "Format each insight as: <insight text> (because of <comma-separated source numbers>)\n"
    "Reply with one insight per line, numbered 1, 2, 3, 4, 5."
)

DEFAULT_REFLECTION_THRESHOLD = 150
DEFAULT_SCORE_BATCH_SIZE = 20
DEFAULT_RETRIEVAL_LIMIT = 3
DEFAULT_SYNTHESIS_MEMORY_LIMIT = 30
SCORING_MODEL_DEFAULT = "claude-haiku-4-5-20251001"
SYNTHESIS_MODEL_DEFAULT = "claude-sonnet-4-6"
EMBEDDING_MODEL = "voyage-3.5-lite"


@dataclass
class ScoredObservation:
    action_id: int
    text: str
    importance: int
    decision: str
    target_persona_id: Optional[str]
    channel_id: int


@dataclass
class ReflectStats:
    """What happened during one maybe_reflect run."""
    persona_id: str
    scored_count: int = 0
    accumulated: int = 0
    threshold: int = DEFAULT_REFLECTION_THRESHOLD
    questions: list[str] = None  # type: ignore[assignment]
    reflections_stored: int = 0
    skipped_reason: Optional[str] = None

    def __post_init__(self):
        if self.questions is None:
            self.questions = []


# ---------------------------------------------------------------
# Helpers (module-level, easier to test in isolation)
# ---------------------------------------------------------------

_IMPORTANCE_RE = re.compile(r"\b([1-9]|10)\b")


def parse_importance(text: str) -> int:
    """Extract a 1-10 integer from the model's response. Falls back to 5 on
    parse failure (mid-poignancy rather than 0; we shouldn't bias against
    scoring just because the LLM was chatty)."""
    if not text:
        return 5
    match = _IMPORTANCE_RE.search(text.strip())
    if match is None:
        return 5
    try:
        return max(1, min(10, int(match.group(1))))
    except (TypeError, ValueError):
        return 5


_QUESTION_LINE_RE = re.compile(r"^\s*\d+[\.\)]\s*(.+?)\s*$")


def parse_questions(text: str) -> list[str]:
    """Extract numbered questions, returning at most 3."""
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _QUESTION_LINE_RE.match(line)
        if m:
            q = m.group(1).strip()
            if q and not q.endswith("?") and len(q) > 5:
                # Tolerate questions without trailing '?'
                q = q + "?"
            if q:
                out.append(q)
            if len(out) >= 3:
                break
    return out


_INSIGHT_RE = re.compile(
    r"^\s*\d+[\.\)]\s*(?P<text>.+?)\s*\(because of\s*(?P<cites>[\d,\s]+)\)\s*$",
    re.IGNORECASE,
)


def parse_insights(text: str) -> list[dict[str, Any]]:
    """Extract insights with their citation indices (1-based).

    Returns a list of {"text": str, "cite_indices": [int, ...]}.
    Lines that don't match the (because of X, Y) format are accepted with
    empty cite_indices so a non-compliant LLM response still yields content.
    """
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Try strict format first
        m = _INSIGHT_RE.match(line)
        if m:
            cite_indices = [
                int(c) for c in re.split(r"[\s,]+", m.group("cites").strip()) if c.isdigit()
            ]
            out.append({"text": m.group("text").strip(), "cite_indices": cite_indices})
        else:
            # Fallback: numbered line without the (because of ...) suffix
            m2 = re.match(r"^\d+[\.\)]\s*(.+)$", line)
            if m2:
                out.append({"text": m2.group(1).strip(), "cite_indices": []})
        if len(out) >= 5:
            break
    return out


def observation_text(action: dict[str, Any]) -> str:
    """Render a proactive_actions row as a 'memory' string for scoring/synthesis."""
    decision = action.get("decision", "?")
    reasoning = (action.get("reasoning") or "").strip() or "(no reasoning)"
    target = action.get("target_persona_id")
    emoji = action.get("emoji")
    bits = [f"decided to {decision}"]
    if target:
        bits.append(f"targeting persona @{target}")
    if emoji:
        bits.append(f"with emoji {emoji}")
    return f"{', '.join(bits)}. Reasoning: {reasoning}"


def infer_subject(action: dict[str, Any]) -> tuple[str, str]:
    """Return (subject_type, subject_id) for a given action.

    Heuristic:
      - action engages or replies to a known persona -> ('persona', target_persona_id)
      - action targets a message author user -> ('user', '<author_id>')   [if available]
      - otherwise -> ('channel', '<channel_id>')
    """
    target_persona = action.get("target_persona_id")
    if target_persona:
        return ("persona", str(target_persona))
    return ("channel", str(action.get("channel_id", "0")))


# ---------------------------------------------------------------
# Engine
# ---------------------------------------------------------------

class ReflectionEngine:
    """
    Orchestrates importance scoring, reflection synthesis, and retrieval.

    Construction takes a db pool; the LLM client is passed per-call so we
    don't capture stale references and so the engine can be unit-tested
    without an Anthropic dependency.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        threshold: int = DEFAULT_REFLECTION_THRESHOLD,
    ):
        self.db = db_pool
        self.threshold = threshold
        self._voyage = None  # lazy

    # ------------------------------------------------------------------
    # Voyage embedding (lazy initialization)
    # ------------------------------------------------------------------

    async def _embed(self, text: str, input_type: str = "document") -> Optional[list[float]]:
        """Return a 1024-dim embedding via Voyage, or None if Voyage isn't configured."""
        if self._voyage is None:
            try:
                import voyageai

                if not os.getenv("VOYAGE_API_KEY"):
                    return None
                self._voyage = voyageai.AsyncClient()
            except ImportError:
                return None
            except Exception as e:
                logger.warning(f"Voyage init failed: {e}")
                return None
        try:
            result = await self._voyage.embed(
                [text], model=EMBEDDING_MODEL, input_type=input_type
            )
            return list(result.embeddings[0])
        except Exception as e:
            logger.warning(f"Voyage embed failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Importance scoring
    # ------------------------------------------------------------------

    async def score_importance(self, text: str, anthropic_client) -> int:
        """Park's importance prompt. Returns 1-10."""
        if anthropic_client is None:
            return 5
        try:
            resp = await anthropic_client.messages.create(
                model=SCORING_MODEL_DEFAULT,
                max_tokens=10,
                system="You return a single integer 1-10. No explanation.",
                messages=[
                    {"role": "user", "content": PARK_IMPORTANCE_PROMPT.format(memory=text)}
                ],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    return parse_importance(block.text)
        except Exception as e:
            logger.warning(f"score_importance failed: {e}")
        return 5

    async def score_unscored_actions(
        self,
        persona_id: str,
        anthropic_client,
        batch_size: int = DEFAULT_SCORE_BATCH_SIZE,
    ) -> int:
        """Score recent unscored proactive_actions for this persona.

        Returns the number of rows newly scored. Bounded by batch_size to
        keep heartbeat ticks short.
        """
        rows = await self.db.fetch(
            """
            SELECT id, decision, target_persona_id, emoji, channel_id, reasoning
            FROM proactive_actions
            WHERE persona_id = $1
              AND importance IS NULL
              AND decision != 'none'
            ORDER BY created_at ASC
            LIMIT $2
            """,
            persona_id,
            batch_size,
        )
        if not rows:
            return 0
        scored = 0
        for row in rows:
            action = dict(row)
            try:
                score = await self.score_importance(observation_text(action), anthropic_client)
                await self.db.execute(
                    "UPDATE proactive_actions SET importance = $2 WHERE id = $1",
                    action["id"],
                    score,
                )
                scored += 1
            except Exception as e:
                logger.warning(f"score_unscored_actions: failed on action {action['id']}: {e}")
        return scored

    async def accumulated_importance_since_last_reflection(
        self, persona_id: str
    ) -> int:
        """Sum of importance scores since the persona's most recent reflection.

        If no reflection has been stored yet, sums everything scored.
        """
        # Use the most recent reflection's created_at as the cutoff.
        cutoff = await self.db.fetchval(
            """
            SELECT MAX(created_at) FROM agent_reflections WHERE persona_id = $1
            """,
            persona_id,
        )
        if cutoff is None:
            row = await self.db.fetchrow(
                """
                SELECT COALESCE(SUM(importance), 0)::INT AS total
                FROM proactive_actions
                WHERE persona_id = $1 AND importance IS NOT NULL
                """,
                persona_id,
            )
        else:
            row = await self.db.fetchrow(
                """
                SELECT COALESCE(SUM(importance), 0)::INT AS total
                FROM proactive_actions
                WHERE persona_id = $1
                  AND importance IS NOT NULL
                  AND created_at > $2
                """,
                persona_id,
                cutoff,
            )
        return int(row["total"]) if row else 0

    async def should_reflect(self, persona_id: str) -> bool:
        return (
            await self.accumulated_importance_since_last_reflection(persona_id)
        ) >= self.threshold

    # ------------------------------------------------------------------
    # Synthesis pipeline
    # ------------------------------------------------------------------

    async def fetch_synthesis_memories(
        self,
        persona_id: str,
        limit: int = DEFAULT_SYNTHESIS_MEMORY_LIMIT,
    ) -> list[dict[str, Any]]:
        """Pull the persona's most recent scored actions for synthesis."""
        rows = await self.db.fetch(
            """
            SELECT id, decision, target_persona_id, emoji, channel_id,
                   reasoning, importance, created_at
            FROM proactive_actions
            WHERE persona_id = $1
              AND importance IS NOT NULL
              AND decision != 'none'
            ORDER BY created_at DESC
            LIMIT $2
            """,
            persona_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def salient_questions(
        self, memories: list[dict[str, Any]], anthropic_client
    ) -> list[str]:
        """Park's salient-questions prompt over the memory stream."""
        if anthropic_client is None or not memories:
            return []
        statements = "\n".join(
            f"{i+1}. {observation_text(m)}" for i, m in enumerate(memories)
        )
        try:
            resp = await anthropic_client.messages.create(
                model=SYNTHESIS_MODEL_DEFAULT,
                max_tokens=300,
                system="You return 3 numbered questions, one per line. No prose outside the list.",
                messages=[
                    {
                        "role": "user",
                        "content": PARK_SALIENT_QUESTIONS_PROMPT.format(statements=statements),
                    }
                ],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    return parse_questions(block.text)
        except Exception as e:
            logger.warning(f"salient_questions failed: {e}")
        return []

    async def synthesize_for_question(
        self,
        question: str,
        candidate_memories: list[dict[str, Any]],
        subject: str,
        anthropic_client,
    ) -> list[dict[str, Any]]:
        """For one salient question, ask the model to synthesize 5 insights with citations."""
        if anthropic_client is None or not candidate_memories:
            return []
        numbered = "\n".join(
            f"{i+1}. {observation_text(m)}" for i, m in enumerate(candidate_memories)
        )
        try:
            resp = await anthropic_client.messages.create(
                model=SYNTHESIS_MODEL_DEFAULT,
                max_tokens=600,
                system=(
                    "You synthesize concise insights about a subject from numbered "
                    "statements. Each insight cites which source statements support it."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Salient question: {question}\n\n"
                            + PARK_SYNTHESIS_PROMPT.format(
                                subject=subject, numbered_memories=numbered
                            )
                        ),
                    }
                ],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    return parse_insights(block.text)
        except Exception as e:
            logger.warning(f"synthesize_for_question failed: {e}")
        return []

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def store_reflection(
        self,
        *,
        persona_id: str,
        subject_type: str,
        subject_id: str,
        content: str,
        importance: int,
        cites: list[dict[str, Any]],
        anthropic_client=None,  # unused; kept for signature parity
        confidence: float = 0.7,
        parent_reflection_id: Optional[int] = None,
    ) -> Optional[int]:
        """Embed the content via Voyage and insert a row into agent_reflections.

        If embedding fails (Voyage not configured), still inserts the row with
        embedding=NULL — retrieval will only consider this row when subject_filter
        matches exactly (no vector search possible).
        """
        embedding = await self._embed(content, input_type="document")

        new_id = await self.db.fetchval(
            """
            INSERT INTO agent_reflections
                (persona_id, subject_type, subject_id, content, embedding,
                 importance, confidence, cites, parent_reflection_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
            RETURNING id
            """,
            persona_id,
            subject_type,
            subject_id,
            content,
            embedding,
            int(max(1, min(10, importance))),
            float(max(0.0, min(1.0, confidence))),
            json.dumps(cites),
            parent_reflection_id,
        )
        return int(new_id) if new_id is not None else None

    # ------------------------------------------------------------------
    # Retrieval (used by observer to fill DeciderInput.reflections_about_others)
    # ------------------------------------------------------------------

    async def retrieve_about(
        self,
        *,
        persona_id: str,
        query: str,
        subject_filter: Optional[list[str]] = None,
        limit: int = DEFAULT_RETRIEVAL_LIMIT,
    ) -> list[str]:
        """Return up to `limit` reflection content strings ranked by relevance.

        Uses cosine similarity on Voyage embeddings; falls back to recency-
        ordered subject_filter match when embedding fails.
        """
        embedding = await self._embed(query, input_type="query")

        if embedding is None or not subject_filter:
            # Fallback: pure recency, optionally filtered by subject_id
            if subject_filter:
                rows = await self.db.fetch(
                    """
                    SELECT content, last_retrieved_at
                    FROM agent_reflections
                    WHERE persona_id = $1
                      AND subject_id = ANY($2::text[])
                    ORDER BY created_at DESC
                    LIMIT $3
                    """,
                    persona_id,
                    list(subject_filter),
                    limit,
                )
            else:
                rows = await self.db.fetch(
                    """
                    SELECT content, last_retrieved_at
                    FROM agent_reflections
                    WHERE persona_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    persona_id,
                    limit,
                )
            ids_to_touch: list[Any] = []
            out = [r["content"] for r in rows]
        else:
            rows = await self.db.fetch(
                """
                SELECT id, content, embedding <=> $2::vector AS distance
                FROM agent_reflections
                WHERE persona_id = $1
                  AND subject_id = ANY($3::text[])
                  AND embedding IS NOT NULL
                ORDER BY distance ASC
                LIMIT $4
                """,
                persona_id,
                embedding,
                list(subject_filter),
                limit,
            )
            ids_to_touch = [r["id"] for r in rows]
            out = [r["content"] for r in rows]

        # Lightweight retrieval bookkeeping (best-effort; non-blocking)
        if ids_to_touch:
            try:
                await self.db.execute(
                    """
                    UPDATE agent_reflections
                    SET last_retrieved_at = NOW(), retrieval_count = retrieval_count + 1
                    WHERE id = ANY($1::bigint[])
                    """,
                    ids_to_touch,
                )
            except Exception as e:
                logger.debug(f"retrieve_about bookkeeping failed: {e}")

        return out

    # ------------------------------------------------------------------
    # Orchestrator (full pipeline)
    # ------------------------------------------------------------------

    async def maybe_reflect(
        self,
        persona_id: str,
        anthropic_client,
        force: bool = False,
    ) -> ReflectStats:
        """Score recent actions, check threshold, synthesize + store reflections.

        Called inline at the end of each heartbeat tick (try/except wraps it so
        a reflection failure doesn't kill the heartbeat). Operators can also
        force-trigger via /proactive reflect.
        """
        stats = ReflectStats(persona_id=persona_id, threshold=self.threshold)

        # 1. Retroactively score any unscored actions
        try:
            stats.scored_count = await self.score_unscored_actions(
                persona_id, anthropic_client
            )
        except Exception as e:
            logger.warning(f"[{persona_id}] score_unscored_actions failed: {e}")

        # 2. Check threshold
        try:
            stats.accumulated = await self.accumulated_importance_since_last_reflection(
                persona_id
            )
        except Exception as e:
            logger.warning(f"[{persona_id}] accumulated_importance failed: {e}")

        if not force and stats.accumulated < self.threshold:
            stats.skipped_reason = "threshold_not_reached"
            return stats

        # 3. Pull memories for synthesis
        memories = await self.fetch_synthesis_memories(persona_id)
        if not memories:
            stats.skipped_reason = "no_scored_memories"
            return stats

        # 4. Generate salient questions
        questions = await self.salient_questions(memories, anthropic_client)
        stats.questions = questions
        if not questions:
            stats.skipped_reason = "no_questions_generated"
            return stats

        # 5. For each question, synthesize insights and store
        for question in questions:
            insights = await self.synthesize_for_question(
                question=question,
                candidate_memories=memories,
                subject=persona_id,
                anthropic_client=anthropic_client,
            )
            for insight in insights:
                # Pick a representative source action from the cited indices
                # (1-based) and use it to infer subject. Falls back to ('self', persona_id).
                cite_indices = insight.get("cite_indices", [])
                anchor_action = (
                    memories[cite_indices[0] - 1]
                    if cite_indices and 0 < cite_indices[0] <= len(memories)
                    else None
                )
                if anchor_action:
                    subject_type, subject_id = infer_subject(anchor_action)
                else:
                    subject_type, subject_id = ("self", persona_id)

                cites = [
                    {"type": "action", "id": memories[idx - 1]["id"]}
                    for idx in cite_indices
                    if 0 < idx <= len(memories)
                ]
                # Score the importance of the insight itself
                importance = await self.score_importance(insight["text"], anthropic_client)
                try:
                    await self.store_reflection(
                        persona_id=persona_id,
                        subject_type=subject_type,
                        subject_id=subject_id,
                        content=insight["text"],
                        importance=importance,
                        cites=cites,
                    )
                    stats.reflections_stored += 1
                except Exception as e:
                    logger.warning(f"[{persona_id}] store_reflection failed: {e}")

        return stats
