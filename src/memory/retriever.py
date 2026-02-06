# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing: [slashdaemon@protonmail.com]

"""
Memory Retrieval

Hybrid search combining lexical (full-text) and semantic (vector) search
using Reciprocal Rank Fusion (RRF) for optimal recall across query types.

Lexical search excels at exact term matching (player names, coordinates, mod names)
while semantic search handles conceptual queries. RRF combines both result sets
by rank position, naturally boosting documents that appear in both.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg
import discord
import voyageai

from .config import MemoryConfig
from .privacy import PrivacyLevel, classify_channel_privacy

logger = logging.getLogger("slashAI.memory")


@dataclass
class RetrievedMemory:
    """A memory retrieved from the database."""

    id: int
    user_id: int  # Discord user ID who owns this memory
    summary: str
    raw_dialogue: str
    memory_type: str
    privacy_level: PrivacyLevel
    similarity: float
    confidence: float  # Extraction confidence (0.0-1.0)
    updated_at: datetime


class MemoryRetriever:
    """Retrieves relevant memories with hybrid lexical + semantic search."""

    def __init__(self, db_pool: asyncpg.Pool, config: MemoryConfig):
        self.db = db_pool
        self.voyage = voyageai.AsyncClient()  # Uses VOYAGE_API_KEY env var
        self.config = config
        self._hybrid_available: bool | None = None  # Cached check for hybrid search

    async def retrieve(
        self,
        user_id: int,
        query: str,
        channel: discord.abc.Messageable,
        top_k: Optional[int] = None,
    ) -> list[RetrievedMemory]:
        """
        Retrieve relevant memories using hybrid search with privacy filtering.

        Combines lexical (BM25-style) and semantic (embedding) search using
        Reciprocal Rank Fusion for optimal recall across query types.

        Args:
            user_id: Discord user ID
            query: Search query (usually current message)
            channel: Discord channel for privacy context
            top_k: Number of memories to retrieve (default from config)

        Returns:
            List of relevant memories, privacy-filtered
        """
        if not query or not query.strip():
            return []

        top_k = top_k or self.config.top_k
        context_privacy = await classify_channel_privacy(channel)

        # Get channel/guild IDs for privacy filtering
        guild = getattr(channel, 'guild', None)
        guild_id = guild.id if guild else None
        channel_id = getattr(channel, 'id', None)

        logger.info(f"Retrieval context: privacy={context_privacy.value}, guild={guild_id}, channel={channel_id}")

        # Generate query embedding
        embedding = await self._embed(query, input_type="query")

        # Try hybrid search if enabled and available
        if self.config.hybrid_search_enabled and await self._is_hybrid_available():
            rows = await self._retrieve_hybrid(
                query, embedding, user_id, context_privacy.value,
                guild_id, channel_id, top_k
            )
        else:
            # Fallback to semantic-only search
            rows = await self._retrieve_semantic(
                embedding, user_id, context_privacy, channel, top_k
            )

        # Reinforce retrieved memories (update access time, boost confidence, increment count)
        if rows:
            ids = [r["id"] for r in rows]
            await self._reinforce_memories(ids)

        memories = [
            RetrievedMemory(
                id=r["id"],
                user_id=r["user_id"],
                summary=r["topic_summary"],
                raw_dialogue=r["raw_dialogue"],
                memory_type=r["memory_type"],
                privacy_level=PrivacyLevel(r["privacy_level"]),
                # Apply reaction boost to similarity (v0.12.0)
                similarity=self._apply_reaction_boost(r["similarity"], r),
                confidence=r["confidence"] or 0.5,
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

        # Re-sort by boosted similarity (reaction-engaged memories rank higher)
        memories.sort(key=lambda m: m.similarity, reverse=True)

        if memories:
            query_preview = query[:50] + "..." if len(query) > 50 else query
            rrf_info = ""
            if rows and "rrf_score" in rows[0].keys():
                rrf_info = ", ".join(
                    f"RRF={r['rrf_score']:.4f}" for r in rows[:3]
                )
                logger.debug(f"Hybrid search scores: {rrf_info}")

            logger.debug(
                f"Retrieved {len(memories)} memories for query '{query_preview}':\n" +
                "\n".join(
                    f"  - Memory {m.id} (user_id={m.user_id}, sim={m.similarity:.3f}): "
                    f"{m.summary[:60]}{'...' if len(m.summary) > 60 else ''}"
                    for m in memories
                )
            )

        return memories

    async def _is_hybrid_available(self) -> bool:
        """Check if hybrid search is available (tsv column and function exist)."""
        if self._hybrid_available is not None:
            return self._hybrid_available

        try:
            # Check if tsv column exists
            result = await self.db.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'memories' AND column_name = 'tsv'
                )
            """)
            self._hybrid_available = result
            if not result:
                logger.warning("Hybrid search unavailable: tsv column not found. Run migration 012.")
            return result
        except Exception as e:
            logger.warning(f"Hybrid search check failed: {e}")
            self._hybrid_available = False
            return False

    async def _retrieve_hybrid(
        self,
        query: str,
        embedding: list[float],
        user_id: int,
        context_privacy: str,
        guild_id: Optional[int],
        channel_id: Optional[int],
        top_k: int,
    ) -> list[asyncpg.Record]:
        """Execute hybrid search using the SQL function."""
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        try:
            rows = await self.db.fetch(
                """SELECT * FROM hybrid_memory_search($1, $2::vector, $3, $4, $5, $6, $7, $8)""",
                query,
                embedding_str,
                user_id,
                context_privacy,
                guild_id,
                channel_id,
                top_k,
                self.config.hybrid_candidate_limit,
            )
            logger.info(f"Hybrid search returned {len(rows)} results")
            return rows
        except Exception as e:
            logger.error(f"Hybrid search failed, falling back to semantic: {e}")
            # Mark hybrid as unavailable to avoid repeated failures
            self._hybrid_available = False
            return []

    async def _retrieve_semantic(
        self,
        embedding: list[float],
        user_id: int,
        context_privacy: PrivacyLevel,
        channel: discord.abc.Messageable,
        top_k: int,
    ) -> list[asyncpg.Record]:
        """Fallback semantic-only search."""
        sql, params = self._build_privacy_query(
            user_id, embedding, context_privacy, channel, top_k
        )
        rows = await self.db.fetch(sql, *params)
        logger.info(f"Semantic search returned {len(rows)} results")
        return rows

    def _build_privacy_query(
        self,
        user_id: int,
        embedding: list[float],
        context_privacy: PrivacyLevel,
        channel: discord.abc.Messageable,
        top_k: int,
    ) -> tuple[str, list]:
        """
        Build SQL query with privacy filtering.

        Privacy rules:
        - DM context: All user memories visible
        - Restricted channel: global + same-guild public + same-channel restricted
        - Public channel: global + same-guild public
        """
        # Convert embedding list to string format for pgvector
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        base_query = """
            SELECT
                id, user_id, topic_summary, raw_dialogue, memory_type, privacy_level,
                confidence, 1 - (embedding <=> $1::vector) as similarity, updated_at,
                reaction_summary
            FROM memories
            WHERE 1 - (embedding <=> $1::vector) > $3
              AND ({privacy_filter})
            ORDER BY embedding <=> $1::vector
            LIMIT $4
        """

        if context_privacy == PrivacyLevel.DM:
            # DM context: all user's memories visible (user is only viewer)
            privacy_filter = "user_id = $2"
            params = [embedding_str, user_id, self.config.similarity_threshold, top_k]

        elif context_privacy == PrivacyLevel.CHANNEL_RESTRICTED:
            # Restricted channel: user's global + ANY user's guild_public + user's channel_restricted
            guild_id = channel.guild.id
            channel_id = channel.id
            privacy_filter = """
                (user_id = $2 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
                OR (user_id = $2 AND privacy_level = 'channel_restricted' AND origin_channel_id = $6)
            """
            params = [
                embedding_str,
                user_id,
                self.config.similarity_threshold,
                top_k,
                guild_id,
                channel_id,
            ]

        else:  # GUILD_PUBLIC
            # Public channel: user's global + ANY user's guild_public from same guild
            guild_id = channel.guild.id
            privacy_filter = """
                (user_id = $2 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
            """
            params = [
                embedding_str,
                user_id,
                self.config.similarity_threshold,
                top_k,
                guild_id,
            ]

        return base_query.format(privacy_filter=privacy_filter), params

    async def _embed(self, text: str, input_type: str = "document") -> list[float]:
        """
        Generate embedding using Voyage AI.

        Args:
            text: Text to embed
            input_type: "query" for retrieval queries, "document" for stored memories
        """
        result = await self.voyage.embed(
            [text], model=self.config.embedding_model, input_type=input_type
        )
        return result.embeddings[0]

    async def _reinforce_memories(self, memory_ids: list[int]) -> None:
        """
        Reinforce accessed memories by boosting confidence and incrementing count.

        This implements the "use it or lose it" principle - memories that
        are retrieved often remain strong while unused memories decay.

        Reinforcement varies by memory type:
        - Semantic: +0.05 (cap 0.99) - facts should stay high
        - Procedural: +0.04 (cap 0.97) - patterns reinforced through use
        - Episodic: +0.03 (cap 0.95) - events strengthen but not become facts
        """
        # Check if retrieval_count column exists (migration 013)
        try:
            await self.db.execute(
                """
                UPDATE memories
                SET
                    confidence = LEAST(
                        CASE memory_type
                            WHEN 'semantic' THEN $2
                            WHEN 'procedural' THEN $3
                            ELSE $4  -- episodic
                        END,
                        confidence + CASE memory_type
                            WHEN 'semantic' THEN $5
                            WHEN 'procedural' THEN $6
                            ELSE $7  -- episodic
                        END
                    ),
                    retrieval_count = COALESCE(retrieval_count, 0) + 1,
                    last_accessed_at = NOW()
                WHERE id = ANY($1)
            """,
                memory_ids,
                self.config.reinforcement_cap_semantic,
                self.config.reinforcement_cap_procedural,
                self.config.reinforcement_cap_episodic,
                self.config.reinforcement_boost_semantic,
                self.config.reinforcement_boost_procedural,
                self.config.reinforcement_boost_episodic,
            )
        except Exception as e:
            # Fallback if retrieval_count column doesn't exist yet
            logger.debug(f"Reinforcement with count failed, using simple update: {e}")
            await self.db.execute(
                "UPDATE memories SET last_accessed_at = NOW() WHERE id = ANY($1)",
                memory_ids,
            )

    def _apply_reaction_boost(self, similarity: float, memory: asyncpg.Record) -> float:
        """
        Boost retrieval score based on reaction engagement (v0.12.0).

        Memories with positive reactions from other users rank higher,
        reflecting community validation of the content.

        Formula: boosted = similarity * (1 + reaction_boost)
        Where reaction_boost = min(0.15, log10(total + 1) * 0.05 * sentiment)
        Capped at 15% boost to avoid overwhelming semantic relevance.

        Args:
            similarity: Base similarity score from vector search
            memory: Database record with reaction_summary JSONB

        Returns:
            Boosted similarity score
        """
        # Check if reaction_summary exists in the record
        try:
            reaction_summary = memory.get("reaction_summary")
        except (KeyError, TypeError):
            # Column doesn't exist or is None
            return similarity

        if not reaction_summary:
            return similarity

        # Extract sentiment and count
        sentiment = reaction_summary.get("sentiment_score", 0)
        total = reaction_summary.get("total_reactions", 0)

        # Only boost for positive sentiment
        if sentiment <= 0 or total == 0:
            return similarity

        # Logarithmic scaling: more reactions = diminishing returns
        # log10(1) = 0, log10(10) = 1, log10(100) = 2
        reaction_boost = min(0.15, math.log10(total + 1) * 0.05 * sentiment)

        return similarity * (1 + reaction_boost)
