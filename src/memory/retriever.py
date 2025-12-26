"""
Memory Retrieval

Semantic search with privacy-aware filtering using Voyage AI embeddings
and pgvector for similarity search.
"""

import logging
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
    summary: str
    raw_dialogue: str
    memory_type: str
    privacy_level: PrivacyLevel
    similarity: float
    updated_at: datetime


class MemoryRetriever:
    """Retrieves relevant memories with privacy filtering."""

    def __init__(self, db_pool: asyncpg.Pool, config: MemoryConfig):
        self.db = db_pool
        self.voyage = voyageai.AsyncClient()  # Uses VOYAGE_API_KEY env var
        self.config = config

    async def retrieve(
        self,
        user_id: int,
        query: str,
        channel: discord.abc.Messageable,
        top_k: Optional[int] = None,
    ) -> list[RetrievedMemory]:
        """
        Retrieve relevant memories with privacy filtering.

        Args:
            user_id: Discord user ID
            query: Search query (usually current message)
            channel: Discord channel for privacy context
            top_k: Number of memories to retrieve (default from config)

        Returns:
            List of relevant memories, privacy-filtered
        """
        top_k = top_k or self.config.top_k
        context_privacy = await classify_channel_privacy(channel)

        # Debug: log retrieval context
        guild_id = getattr(channel, 'guild', None)
        guild_id = guild_id.id if guild_id else None
        channel_id = getattr(channel, 'id', None)
        logger.info(f"Retrieval context: privacy={context_privacy.value}, guild={guild_id}, channel={channel_id}")

        embedding = await self._embed(query, input_type="query")

        sql, params = self._build_privacy_query(
            user_id, embedding, context_privacy, channel, top_k
        )

        # Debug: log available memories for this user
        all_memories = await self.db.fetch(
            "SELECT id, privacy_level, origin_guild_id, topic_summary FROM memories WHERE user_id = $1",
            user_id
        )
        logger.info(f"User has {len(all_memories)} total memories:")
        for m in all_memories:
            logger.info(f"  [{m['privacy_level']}] guild={m['origin_guild_id']}: {m['topic_summary'][:50]}...")

        # Debug: show similarity scores before threshold filter
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
        similarity_check = await self.db.fetch(
            """SELECT topic_summary, privacy_level, origin_guild_id,
                      1 - (embedding <=> $1::vector) as similarity
               FROM memories WHERE user_id = $2
               ORDER BY embedding <=> $1::vector LIMIT 5""",
            embedding_str, user_id
        )
        logger.info(f"Top 5 similarities (before privacy filter, threshold={self.config.similarity_threshold}):")
        for m in similarity_check:
            logger.info(f"  sim={m['similarity']:.3f} [{m['privacy_level']}] {m['topic_summary'][:40]}...")

        rows = await self.db.fetch(sql, *params)

        # Update last_accessed_at for retrieved memories
        if rows:
            ids = [r["id"] for r in rows]
            await self.db.execute(
                "UPDATE memories SET last_accessed_at = NOW() WHERE id = ANY($1)", ids
            )

        return [
            RetrievedMemory(
                id=r["id"],
                summary=r["topic_summary"],
                raw_dialogue=r["raw_dialogue"],
                memory_type=r["memory_type"],
                privacy_level=PrivacyLevel(r["privacy_level"]),
                similarity=r["similarity"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

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
                id, topic_summary, raw_dialogue, memory_type, privacy_level,
                1 - (embedding <=> $1::vector) as similarity, updated_at
            FROM memories
            WHERE user_id = $2
              AND 1 - (embedding <=> $1::vector) > $3
              AND ({privacy_filter})
            ORDER BY embedding <=> $1::vector
            LIMIT $4
        """

        if context_privacy == PrivacyLevel.DM:
            # DM context: all memories visible (user is only viewer)
            privacy_filter = "TRUE"
            params = [embedding_str, user_id, self.config.similarity_threshold, top_k]

        elif context_privacy == PrivacyLevel.CHANNEL_RESTRICTED:
            # Restricted channel: global + same-guild public + same-channel restricted
            guild_id = channel.guild.id
            channel_id = channel.id
            privacy_filter = """
                privacy_level = 'global'
                OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
                OR (privacy_level = 'channel_restricted' AND origin_channel_id = $6)
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
            # Public channel: global + same-guild public
            guild_id = channel.guild.id
            privacy_filter = """
                privacy_level = 'global'
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
