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
Memory Manager

Facade orchestrating memory extraction, retrieval, and update operations
with privacy enforcement.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg
import discord
import voyageai
from anthropic import AsyncAnthropic

from analytics import track

logger = logging.getLogger("slashAI.memory")

from .config import MemoryConfig, ImageMemoryConfig
from .extractor import MemoryExtractor
from .privacy import PrivacyLevel, classify_channel_privacy
from .retriever import MemoryRetriever, RetrievedMemory
from .updater import MemoryUpdater


@dataclass
class RetrievedImage:
    """An image observation retrieved from the database."""

    id: int
    user_id: int
    description: str
    summary: str
    tags: list[str]
    cluster_name: Optional[str]
    similarity: float
    captured_at: datetime
    privacy_level: str


class MemoryManager:
    """Facade for memory operations with privacy enforcement."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        anthropic_client: AsyncAnthropic,
        config: Optional[MemoryConfig] = None,
    ):
        self.config = config or MemoryConfig.from_env()
        self.image_config = ImageMemoryConfig.from_env()
        self.extractor = MemoryExtractor(anthropic_client)
        self.retriever = MemoryRetriever(db_pool, self.config)
        self.updater = MemoryUpdater(
            db_pool, self.retriever, anthropic_client, self.config
        )
        self.db = db_pool
        self._anthropic = anthropic_client
        self._voyage = voyageai.AsyncClient()  # For image embeddings

        # Image memory components (lazy initialized)
        self._image_observer = None
        self._build_narrator = None

    async def retrieve(
        self, user_id: int, query: str, channel: discord.abc.Messageable
    ) -> list[RetrievedMemory]:
        """
        Retrieve relevant memories for a user, privacy-filtered.

        Args:
            user_id: Discord user ID
            query: Search query (usually current message)
            channel: Discord channel for privacy context

        Returns:
            List of relevant memories
        """
        # Handle empty queries (e.g., image-only messages)
        if not query or not query.strip():
            logger.debug("Empty query, skipping memory retrieval")
            return []

        logger.info(f"Retrieving memories for user={user_id}, query={query[:50]}...")
        memories = await self.retriever.retrieve(user_id, query, channel)
        logger.info(f"Retrieved {len(memories)} memories")

        # Analytics: Track retrieval
        guild = getattr(channel, "guild", None)
        track(
            "retrieval_performed",
            "memory",
            user_id=user_id,
            channel_id=getattr(channel, "id", None),
            guild_id=guild.id if guild else None,
            properties={
                "query_length": len(query),
                "results_count": len(memories),
                "top_similarity": memories[0].similarity if memories else 0.0,
            },
        )

        for mem in memories:
            logger.debug(f"  - [{mem.memory_type}] {mem.summary[:50]}... (sim={mem.similarity:.3f})")
        return memories

    async def search(
        self,
        query: str,
        user_id: Optional[int] = None,
        limit: int = 5,
    ) -> list[RetrievedMemory]:
        """
        Search memories by semantic similarity (for agentic tool use).

        Unlike retrieve(), this method doesn't apply privacy filtering since
        it's used by the owner to explicitly query memories.

        Args:
            query: Search query
            user_id: Optional user ID to filter memories by owner
            limit: Max results (default 5, max 10)

        Returns:
            List of matching memories with similarity scores
        """
        limit = min(limit, 10)

        # Generate query embedding
        embedding = await self.retriever._embed(query, input_type="query")
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        # Build query with optional user filter
        if user_id:
            sql = """
                SELECT
                    id, user_id, topic_summary, raw_dialogue, memory_type, privacy_level,
                    confidence, 1 - (embedding <=> $1::vector) as similarity, updated_at
                FROM memories
                WHERE user_id = $2
                  AND 1 - (embedding <=> $1::vector) > $3
                ORDER BY embedding <=> $1::vector
                LIMIT $4
            """
            params = [embedding_str, user_id, self.config.similarity_threshold, limit]
        else:
            sql = """
                SELECT
                    id, user_id, topic_summary, raw_dialogue, memory_type, privacy_level,
                    confidence, 1 - (embedding <=> $1::vector) as similarity, updated_at
                FROM memories
                WHERE 1 - (embedding <=> $1::vector) > $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
            """
            params = [embedding_str, self.config.similarity_threshold, limit]

        rows = await self.db.fetch(sql, *params)

        memories = [
            RetrievedMemory(
                id=r["id"],
                user_id=r["user_id"],
                summary=r["topic_summary"],
                raw_dialogue=r["raw_dialogue"],
                memory_type=r["memory_type"],
                privacy_level=PrivacyLevel(r["privacy_level"]),
                similarity=r["similarity"],
                confidence=r["confidence"] or 0.5,
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

        logger.info(f"Memory search for '{query[:30]}...' returned {len(memories)} results")
        return memories

    async def get_popular_memories(
        self,
        limit: int = 5,
        min_reactions: int = 1,
        sentiment_filter: str = "positive",
        scope: str = "community",
        min_unique_reactors: int = 1,
    ) -> list[dict]:
        """
        Get memories sorted by reaction engagement (v0.12.2, enhanced v0.12.3).

        Args:
            limit: Max results (default 5, max 10)
            min_reactions: Minimum reaction count to include
            sentiment_filter: "positive" for sentiment > 0, "any" for all
            scope: "community" excludes self-reactions, "all" includes everything
            min_unique_reactors: Minimum unique users who reacted

        Returns:
            List of memory dicts with reaction data
        """
        import json as json_module

        limit = min(limit, 10)

        if scope == "community":
            # Query with join to filter out self-reactions (reactor != memory owner)
            sentiment_clause = "AND r.sentiment > 0" if sentiment_filter == "positive" else ""

            sql = f"""
                SELECT
                    m.id, m.user_id, m.topic_summary, m.raw_dialogue, m.memory_type,
                    m.privacy_level, m.confidence, m.updated_at, m.reaction_summary,
                    COUNT(r.id) as reaction_count,
                    COUNT(DISTINCT r.reactor_id) as unique_reactors,
                    AVG(r.sentiment) as avg_sentiment,
                    ARRAY_AGG(DISTINCT r.emoji) as emoji_list
                FROM memories m
                JOIN memory_message_links l ON m.id = l.memory_id
                JOIN message_reactions r ON l.message_id = r.message_id
                WHERE r.removed_at IS NULL
                  AND r.reactor_id != m.user_id
                  {sentiment_clause}
                GROUP BY m.id, m.user_id, m.topic_summary, m.raw_dialogue, m.memory_type,
                         m.privacy_level, m.confidence, m.updated_at, m.reaction_summary
                HAVING COUNT(r.id) >= $1
                   AND COUNT(DISTINCT r.reactor_id) >= $2
                ORDER BY COUNT(r.id) DESC, AVG(r.sentiment) DESC
                LIMIT $3
            """
            rows = await self.db.fetch(sql, min_reactions, min_unique_reactors, limit)
        else:
            # Original query using pre-aggregated reaction_summary
            sentiment_clause = "AND (reaction_summary->>'sentiment_score')::float > 0" if sentiment_filter == "positive" else ""

            sql = f"""
                SELECT
                    id, user_id, topic_summary, raw_dialogue, memory_type, privacy_level,
                    confidence, updated_at, reaction_summary,
                    (reaction_summary->>'total_reactions')::int as reaction_count,
                    (reaction_summary->>'unique_reactors')::int as unique_reactors,
                    (reaction_summary->>'sentiment_score')::float as avg_sentiment,
                    NULL as emoji_list
                FROM memories
                WHERE reaction_summary IS NOT NULL
                  AND (reaction_summary->>'total_reactions')::int >= $1
                  AND COALESCE((reaction_summary->>'unique_reactors')::int, 1) >= $2
                  {sentiment_clause}
                ORDER BY (reaction_summary->>'total_reactions')::int DESC,
                         (reaction_summary->>'sentiment_score')::float DESC
                LIMIT $3
            """
            rows = await self.db.fetch(sql, min_reactions, min_unique_reactors, limit)

        results = []
        for r in rows:
            # Parse reaction_summary if it's a string
            reaction_summary = r.get("reaction_summary")
            if isinstance(reaction_summary, str):
                reaction_summary = json_module.loads(reaction_summary)

            # Build emoji list from query or summary
            emoji_list = r.get("emoji_list")
            if emoji_list:
                top_emoji = [{"emoji": e, "count": 1} for e in emoji_list[:5]]
            elif reaction_summary:
                top_emoji = reaction_summary.get("top_emoji", [])
            else:
                top_emoji = []

            results.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "summary": r["topic_summary"],
                "raw_dialogue": r["raw_dialogue"],
                "memory_type": r["memory_type"],
                "privacy_level": r["privacy_level"],
                "confidence": r["confidence"] or 0.5,
                "updated_at": r["updated_at"],
                "reaction_count": r["reaction_count"],
                "unique_reactors": r.get("unique_reactors", 1),
                "sentiment_score": r.get("avg_sentiment", 0),
                "reaction_summary": {"top_emoji": top_emoji},
            })

        logger.info(f"Popular memories query (scope={scope}) returned {len(results)} results")
        return results

    async def create_community_observation(
        self,
        message_id: int,
        channel_id: int,
        guild_id: int,
        author_id: int,
        content: str,
    ) -> Optional[int]:
        """
        Create a community observation memory from a reacted message (v0.12.4).

        This enables reaction-triggered passive observation of community content.
        When a message receives a reaction but has no memory link, we create a
        lightweight "community_observation" memory to capture the content.

        Args:
            message_id: Discord message ID
            channel_id: Discord channel ID
            guild_id: Discord guild ID
            author_id: Message author's user ID
            content: Message content

        Returns:
            Memory ID if created, None on failure or if already linked
        """
        try:
            # Double-check no link exists (race condition protection)
            existing = await self.db.fetchval(
                "SELECT EXISTS(SELECT 1 FROM memory_message_links WHERE message_id = $1)",
                message_id,
            )
            if existing:
                return None

            # Truncate content for summary (keep it concise)
            summary = content[:500] if len(content) > 500 else content

            # Generate embedding for the content
            embedding = await self.retriever._embed(summary, input_type="document")
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            # Create the memory
            memory_id = await self.db.fetchval(
                """
                INSERT INTO memories (
                    user_id, topic_summary, raw_dialogue, memory_type,
                    privacy_level, confidence, origin_guild_id, origin_channel_id, embedding
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector)
                RETURNING id
                """,
                author_id,
                summary,
                content,
                "community_observation",
                "guild_public",
                0.5,  # Moderate confidence (not LLM-extracted)
                guild_id,
                channel_id,
                embedding_str,
            )

            if memory_id:
                # Create memory-message link
                await self.db.execute(
                    """
                    INSERT INTO memory_message_links (memory_id, message_id, channel_id, contribution_type)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (memory_id, message_id) DO NOTHING
                    """,
                    memory_id,
                    message_id,
                    channel_id,
                    "community_observation",
                )
                logger.info(
                    f"Created community observation memory {memory_id} for message {message_id} "
                    f"(author={author_id}, channel={channel_id})"
                )

                # Track analytics
                track(
                    "community_observation_created",
                    "memory",
                    user_id=author_id,
                    channel_id=channel_id,
                    guild_id=guild_id,
                    properties={
                        "memory_id": memory_id,
                        "message_id": message_id,
                        "content_length": len(content),
                    },
                )

            return memory_id

        except Exception as e:
            logger.error(f"Error creating community observation: {e}", exc_info=True)
            return None

    async def get_build_context(
        self, user_id: int, channel: discord.abc.Messageable
    ) -> str:
        """
        Get build context for injection into chat responses.

        Args:
            user_id: Discord user ID
            channel: Discord channel for privacy context

        Returns:
            Formatted markdown string with build context, or empty string
        """
        if not self._build_narrator:
            # Lazy import to avoid circular dependencies
            from .images.narrator import BuildNarrator

            self._build_narrator = BuildNarrator(self.db, self._anthropic)

        privacy_level = await classify_channel_privacy(channel)
        guild = getattr(channel, "guild", None)
        guild_id = guild.id if guild else None

        return await self._build_narrator.get_brief_context(
            user_id, privacy_level.value, guild_id
        )

    async def retrieve_images(
        self,
        user_id: int,
        query: str,
        channel: discord.abc.Messageable,
        top_k: int = 5,
    ) -> list[RetrievedImage]:
        """
        Retrieve relevant image observations by semantic search.

        Args:
            user_id: Discord user ID
            query: Search query (usually current message)
            channel: Discord channel for privacy context
            top_k: Number of images to retrieve

        Returns:
            List of relevant images, privacy-filtered
        """
        if not query or not query.strip():
            return []

        # Get privacy context
        context_privacy = await classify_channel_privacy(channel)
        guild = getattr(channel, "guild", None)
        guild_id = guild.id if guild else None
        channel_id = getattr(channel, "id", None)

        # Embed query using multimodal model (same as image embeddings)
        # Note: Must use multimodal_embed() with text input, not embed()
        # voyage-multimodal-3 embeds both images and text in the same space
        result = await self._voyage.multimodal_embed(
            inputs=[[query]],  # Text wrapped in list for multimodal API
            model=self.image_config.image_embedding_model,
        )
        embedding = result.embeddings[0]
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        # Build privacy-filtered query
        # Use image-calibrated threshold (0.15 minimum, much lower than text)
        threshold = self.image_config.image_minimum_relevance

        if context_privacy == PrivacyLevel.DM:
            # DM: user's own images only
            privacy_filter = "io.user_id = $2"
            params = [embedding_str, user_id, threshold, top_k]
        elif context_privacy == PrivacyLevel.CHANNEL_RESTRICTED:
            # Restricted: user's global + guild_public + user's channel_restricted
            privacy_filter = """
                (io.user_id = $2 AND io.privacy_level = 'global')
                OR (io.privacy_level = 'guild_public' AND io.guild_id = $5)
                OR (io.user_id = $2 AND io.privacy_level = 'channel_restricted' AND io.channel_id = $6)
            """
            params = [embedding_str, user_id, threshold, top_k, guild_id, channel_id]
        else:  # GUILD_PUBLIC
            # Public: user's global + any guild_public from same guild
            privacy_filter = """
                (io.user_id = $2 AND io.privacy_level = 'global')
                OR (io.privacy_level = 'guild_public' AND io.guild_id = $5)
            """
            params = [embedding_str, user_id, threshold, top_k, guild_id]

        sql = f"""
            SELECT
                io.id, io.user_id, io.description, io.summary, io.tags,
                io.privacy_level, io.captured_at,
                bc.auto_name as cluster_name, bc.user_name as cluster_user_name,
                1 - (io.embedding <=> $1::vector) as similarity
            FROM image_observations io
            LEFT JOIN build_clusters bc ON io.build_cluster_id = bc.id
            WHERE 1 - (io.embedding <=> $1::vector) > $3
              AND ({privacy_filter})
            ORDER BY io.embedding <=> $1::vector
            LIMIT $4
        """

        rows = await self.db.fetch(sql, *params)

        images = [
            RetrievedImage(
                id=r["id"],
                user_id=r["user_id"],
                description=r["description"] or "",
                summary=r["summary"] or "",
                tags=r["tags"] or [],
                cluster_name=r["cluster_user_name"] or r["cluster_name"],
                similarity=r["similarity"],
                captured_at=r["captured_at"],
                privacy_level=r["privacy_level"],
            )
            for r in rows
        ]

        logger.info(
            f"Image retrieval for '{query[:30]}...' returned {len(images)} results "
            f"(threshold={threshold}, context={context_privacy.value})"
        )
        for img in images:
            logger.debug(f"  - [{img.similarity:.3f}] {img.summary[:50]}...")

        return images

    async def track_message(
        self,
        user_id: int,
        channel_id: int,
        channel: discord.abc.Messageable,
        user_message: str,
        assistant_message: str,
        user_message_id: Optional[int] = None,
        assistant_message_id: Optional[int] = None,
    ):
        """
        Track a message exchange for future extraction.

        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            channel: Discord channel object
            user_message: User's message content
            assistant_message: Bot's response content
            user_message_id: Discord message ID of user's message (v0.12.0)
            assistant_message_id: Discord message ID of bot's response (v0.12.0)
        """
        channel_privacy = await classify_channel_privacy(channel)
        guild = getattr(channel, "guild", None)
        guild_id = guild.id if guild else None

        logger.info(f"Tracking message for user={user_id}, channel={channel_id}, privacy={channel_privacy.value}")

        session = await self._get_or_create_session(
            user_id, channel_id, guild_id, channel_privacy
        )

        # Append new messages to session
        # Handle both list (correct) and string (legacy double-encoded) formats
        raw_messages = session["messages"]
        if isinstance(raw_messages, str):
            messages = json.loads(raw_messages) if raw_messages else []
        else:
            messages = raw_messages or []
        # Include message IDs for reaction linking (v0.12.0)
        messages.append({
            "role": "user",
            "content": user_message,
            "message_id": user_message_id,
        })
        messages.append({
            "role": "assistant",
            "content": assistant_message,
            "message_id": assistant_message_id,
        })

        await self.db.execute(
            """UPDATE memory_sessions SET messages = $1::jsonb, message_count = message_count + 2,
               last_activity_at = NOW() WHERE user_id = $2 AND channel_id = $3""",
            json.dumps(messages),
            user_id,
            channel_id,
        )

        msg_count = len(messages) // 2
        threshold = self.config.extraction_message_threshold
        logger.info(f"Session has {msg_count}/{threshold} message exchanges")

        # Check if we should trigger extraction
        # Threshold is per-message, but we store pairs, so multiply by 2
        if len(messages) >= self.config.extraction_message_threshold * 2:
            logger.info(f"Threshold reached, triggering extraction for user={user_id}")
            await self._trigger_extraction(user_id, channel_id, channel, messages)

    async def _get_or_create_session(
        self,
        user_id: int,
        channel_id: int,
        guild_id: Optional[int],
        channel_privacy: PrivacyLevel,
    ) -> dict:
        """Get or create a session for tracking messages."""
        session = await self.db.fetchrow(
            "SELECT * FROM memory_sessions WHERE user_id = $1 AND channel_id = $2",
            user_id,
            channel_id,
        )

        if not session:
            await self.db.execute(
                """INSERT INTO memory_sessions (user_id, channel_id, guild_id, channel_privacy_level)
                   VALUES ($1, $2, $3, $4)""",
                user_id,
                channel_id,
                guild_id,
                channel_privacy.value,
            )
            session = await self.db.fetchrow(
                "SELECT * FROM memory_sessions WHERE user_id = $1 AND channel_id = $2",
                user_id,
                channel_id,
            )

        return dict(session)

    async def _trigger_extraction(
        self,
        user_id: int,
        channel_id: int,
        channel: discord.abc.Messageable,
        messages: list[dict],
    ):
        """Extract memories from accumulated messages."""
        guild = getattr(channel, "guild", None)
        guild_id = guild.id if guild else None
        channel_privacy = await classify_channel_privacy(channel)

        # Analytics: Track extraction triggered
        track(
            "extraction_triggered",
            "memory",
            user_id=user_id,
            channel_id=channel_id,
            guild_id=guild_id,
            properties={
                "message_count": len(messages),
                "channel_privacy": channel_privacy.value,
            },
        )

        try:
            logger.info(f"Extracting memories from {len(messages)} messages")
            extracted_with_privacy = await self.extractor.extract_with_privacy(
                messages, channel
            )
            logger.info(f"Extracted {len(extracted_with_privacy)} memory topics")

            # Extract message IDs for linking (v0.12.0)
            message_ids = [
                m.get("message_id")
                for m in messages
                if m.get("message_id") is not None
            ]

            for memory, privacy_level in extracted_with_privacy:
                logger.info(f"Storing memory: [{privacy_level.value}] {memory.summary[:50]}...")
                memory_id = await self.updater.update(
                    user_id, memory, privacy_level, channel_id, guild_id
                )

                # Link memory to source messages for reaction aggregation (v0.12.0)
                if message_ids:
                    await self._create_memory_message_links(
                        memory_id, message_ids, channel_id
                    )

                # Analytics: Track memory created
                track(
                    "memory_created",
                    "memory",
                    user_id=user_id,
                    channel_id=channel_id,
                    guild_id=guild_id,
                    properties={
                        "memory_type": memory.memory_type,
                        "privacy_level": privacy_level.value,
                        "confidence": memory.confidence,
                        "linked_messages": len(message_ids),
                    },
                )

            # Reset session after extraction
            await self.db.execute(
                """UPDATE memory_sessions SET extracted_at = NOW(), messages = '[]'::jsonb,
                   message_count = 0 WHERE user_id = $1 AND channel_id = $2""",
                user_id,
                channel_id,
            )
            logger.info(f"Session reset for user={user_id}, channel={channel_id}")
        except Exception as e:
            logger.error(f"Memory extraction failed for user={user_id}: {e}", exc_info=True)
            # Analytics: Track extraction failure
            track(
                "extraction_failed",
                "error",
                user_id=user_id,
                channel_id=channel_id,
                guild_id=guild_id,
                properties={
                    "error_type": type(e).__name__,
                    "message_count": len(messages),
                },
            )
            # Don't reset session on failure - will retry next threshold

    async def _create_memory_message_links(
        self,
        memory_id: int,
        message_ids: list[int],
        channel_id: int,
    ) -> None:
        """
        Create links between a memory and its source messages.

        This enables reaction aggregation - reactions on these messages
        will contribute to the memory's confidence/decay calculations.

        Args:
            memory_id: Memory ID
            message_ids: List of Discord message IDs that contributed to this memory
            channel_id: Discord channel ID
        """
        if not message_ids:
            return

        try:
            # Use executemany for efficiency
            for msg_id in message_ids:
                await self.db.execute(
                    """
                    INSERT INTO memory_message_links (memory_id, message_id, channel_id, contribution_type)
                    VALUES ($1, $2, $3, 'source')
                    ON CONFLICT (memory_id, message_id) DO NOTHING
                    """,
                    memory_id,
                    msg_id,
                    channel_id,
                )
            logger.debug(f"Linked memory {memory_id} to {len(message_ids)} messages")
        except Exception as e:
            # Log but don't fail - linking is optional enhancement
            logger.warning(f"Failed to create memory-message links: {e}")

    # =========================================================================
    # Memory Management Commands (v0.9.11)
    # =========================================================================

    async def list_user_memories(
        self,
        user_id: int,
        privacy_filter: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        List memories for a user with pagination.

        Args:
            user_id: Discord user ID
            privacy_filter: Optional privacy level filter (dm, channel_restricted, guild_public, global)
            limit: Max memories to return
            offset: Offset for pagination

        Returns:
            Tuple of (memories list, total count)
        """
        # Build query with optional privacy filter
        if privacy_filter and privacy_filter != "all":
            count_query = """
                SELECT COUNT(*) FROM memories
                WHERE user_id = $1 AND privacy_level = $2
            """
            data_query = """
                SELECT id, topic_summary, memory_type, privacy_level,
                       confidence, created_at, updated_at, last_accessed_at
                FROM memories
                WHERE user_id = $1 AND privacy_level = $2
                ORDER BY updated_at DESC
                LIMIT $3 OFFSET $4
            """
            total = await self.db.fetchval(count_query, user_id, privacy_filter)
            rows = await self.db.fetch(data_query, user_id, privacy_filter, limit, offset)
        else:
            count_query = "SELECT COUNT(*) FROM memories WHERE user_id = $1"
            data_query = """
                SELECT id, topic_summary, memory_type, privacy_level,
                       confidence, created_at, updated_at, last_accessed_at
                FROM memories
                WHERE user_id = $1
                ORDER BY updated_at DESC
                LIMIT $2 OFFSET $3
            """
            total = await self.db.fetchval(count_query, user_id)
            rows = await self.db.fetch(data_query, user_id, limit, offset)

        memories = [dict(row) for row in rows]
        logger.debug(f"Listed {len(memories)}/{total} memories for user={user_id}")
        return memories, total

    async def search_user_memories(
        self,
        user_id: int,
        query: str,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        Search memories for a user by text.

        Args:
            user_id: Discord user ID
            query: Search term
            limit: Max memories to return
            offset: Offset for pagination

        Returns:
            Tuple of (memories list, total count)
        """
        search_pattern = f"%{query}%"

        count_query = """
            SELECT COUNT(*) FROM memories
            WHERE user_id = $1
              AND (topic_summary ILIKE $2 OR raw_dialogue ILIKE $2)
        """
        data_query = """
            SELECT id, topic_summary, memory_type, privacy_level,
                   confidence, updated_at
            FROM memories
            WHERE user_id = $1
              AND (topic_summary ILIKE $2 OR raw_dialogue ILIKE $2)
            ORDER BY updated_at DESC
            LIMIT $3 OFFSET $4
        """

        total = await self.db.fetchval(count_query, user_id, search_pattern)
        rows = await self.db.fetch(data_query, user_id, search_pattern, limit, offset)

        memories = [dict(row) for row in rows]
        logger.debug(f"Search '{query}' returned {len(memories)}/{total} for user={user_id}")
        return memories, total

    async def find_mentions(
        self,
        user_id: int,
        guild_id: int,
        identifiers: list[str],
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        Find public memories from other users that mention this user.

        Args:
            user_id: Discord user ID of the requesting user
            guild_id: Guild ID to search within
            identifiers: List of identifiers to search for (username, display name, IGN)
            limit: Max memories to return
            offset: Offset for pagination

        Returns:
            Tuple of (memories list, total count)
        """
        if not identifiers:
            return [], 0

        # Build OR conditions for each identifier
        conditions = []
        params = [guild_id, user_id]
        param_idx = 3

        for identifier in identifiers:
            pattern = f"%{identifier}%"
            conditions.append(f"(m.topic_summary ILIKE ${param_idx} OR m.raw_dialogue ILIKE ${param_idx})")
            params.append(pattern)
            param_idx += 1

        where_clause = " OR ".join(conditions)

        count_query = f"""
            SELECT COUNT(*) FROM memories m
            WHERE m.privacy_level = 'guild_public'
              AND m.origin_guild_id = $1
              AND m.user_id != $2
              AND ({where_clause})
        """

        data_query = f"""
            SELECT m.id, m.user_id, m.topic_summary, m.memory_type,
                   m.privacy_level, m.updated_at
            FROM memories m
            WHERE m.privacy_level = 'guild_public'
              AND m.origin_guild_id = $1
              AND m.user_id != $2
              AND ({where_clause})
            ORDER BY m.updated_at DESC
            LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """

        params_count = params.copy()
        params_data = params + [limit, offset]

        total = await self.db.fetchval(count_query, *params_count)
        rows = await self.db.fetch(data_query, *params_data)

        memories = [dict(row) for row in rows]
        logger.debug(f"Found {len(memories)}/{total} mentions for user={user_id} in guild={guild_id}")
        return memories, total

    async def get_memory(self, memory_id: int) -> Optional[dict]:
        """
        Get a single memory by ID.

        Args:
            memory_id: Memory ID

        Returns:
            Memory dict or None if not found
        """
        row = await self.db.fetchrow(
            """
            SELECT id, user_id, topic_summary, raw_dialogue, memory_type,
                   privacy_level, confidence, origin_guild_id, origin_channel_id,
                   source_count, created_at, updated_at, last_accessed_at
            FROM memories
            WHERE id = $1
            """,
            memory_id,
        )
        return dict(row) if row else None

    async def delete_memory(self, memory_id: int, user_id: int) -> bool:
        """
        Delete a memory with ownership check.

        Args:
            memory_id: Memory ID to delete
            user_id: User ID who is requesting deletion (must own the memory)

        Returns:
            True if deleted, False if not found or not owned
        """
        # Get memory first for audit logging
        memory = await self.get_memory(memory_id)
        if not memory or memory["user_id"] != user_id:
            logger.warning(f"Delete failed: memory={memory_id} not found or not owned by user={user_id}")
            return False

        # Log deletion to audit table (if it exists)
        try:
            await self.db.execute(
                """
                INSERT INTO memory_deletion_log
                    (memory_id, user_id, topic_summary, privacy_level)
                VALUES ($1, $2, $3, $4)
                """,
                memory_id,
                user_id,
                memory["topic_summary"],
                memory["privacy_level"],
            )
        except Exception as e:
            # Audit table might not exist yet - that's OK
            logger.debug(f"Audit log insert failed (table may not exist): {e}")

        # Delete the memory
        result = await self.db.execute(
            "DELETE FROM memories WHERE id = $1 AND user_id = $2",
            memory_id,
            user_id,
        )

        deleted = result == "DELETE 1"
        if deleted:
            logger.info(f"Deleted memory={memory_id} for user={user_id}: {memory['topic_summary'][:50]}...")
        return deleted

    async def get_user_stats(self, user_id: int) -> dict:
        """
        Get memory statistics for a user.

        Args:
            user_id: Discord user ID

        Returns:
            Dict with stats: total, by_privacy, by_type, last_updated
        """
        # Get counts by privacy level
        privacy_rows = await self.db.fetch(
            """
            SELECT privacy_level, COUNT(*) as count
            FROM memories
            WHERE user_id = $1
            GROUP BY privacy_level
            """,
            user_id,
        )

        # Get counts by type
        type_rows = await self.db.fetch(
            """
            SELECT memory_type, COUNT(*) as count
            FROM memories
            WHERE user_id = $1
            GROUP BY memory_type
            """,
            user_id,
        )

        # Get last updated
        last_updated = await self.db.fetchval(
            "SELECT MAX(updated_at) FROM memories WHERE user_id = $1",
            user_id,
        )

        total = sum(row["count"] for row in privacy_rows)

        return {
            "total": total,
            "by_privacy": {row["privacy_level"]: row["count"] for row in privacy_rows},
            "by_type": {row["memory_type"]: row["count"] for row in type_rows},
            "last_updated": last_updated,
        }
