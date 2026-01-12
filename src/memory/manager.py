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
                    confidence, 1 - (embedding <=> $1::vector) as similarity, updated_at,
                    linked_image_id
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
                    confidence, 1 - (embedding <=> $1::vector) as similarity, updated_at,
                    linked_image_id
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
                linked_image_id=r.get("linked_image_id"),
            )
            for r in rows
        ]

        logger.info(f"Memory search for '{query[:30]}...' returned {len(memories)} results")
        return memories

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
    ):
        """
        Track a message exchange for future extraction.

        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            channel: Discord channel object
            user_message: User's message content
            assistant_message: Bot's response content
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
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": assistant_message})

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

    async def create_image_text_memory(
        self,
        user_id: int,
        observation_id: int,
        description: str,
        summary: str,
        tags: list[str],
        accompanying_text: Optional[str],
        privacy_level: PrivacyLevel,
        channel_id: int,
        guild_id: Optional[int],
    ) -> Optional[int]:
        """
        Create a text memory representation of an image observation.

        This bridges the visual and textual embedding spaces by storing
        the image's description in the text memory system.

        Args:
            user_id: Discord user ID
            observation_id: ID of the image observation
            description: Claude-generated image description
            summary: One-line image summary
            tags: Image categorization tags
            accompanying_text: Discord message text that came with the image
            privacy_level: Privacy level (inherited from image)
            channel_id: Origin channel ID
            guild_id: Origin guild ID

        Returns:
            Memory ID if created, None if error
        """
        try:
            # Combine image metadata into rich text representation
            # Format: summary + description + tags for searchability
            tag_text = ", ".join(tags) if tags else "no tags"

            # Use the summary as the topic (what gets embedded)
            topic = f"Image: {summary}"

            # Build detailed raw dialogue including all context
            raw_parts = [description]
            if tags:
                raw_parts.append(f"Tags: {tag_text}")
            if accompanying_text:
                raw_parts.append(f"Context: {accompanying_text}")
            raw_dialogue = "\n".join(raw_parts)

            # Create ExtractedMemory representation
            from .extractor import ExtractedMemory
            memory = ExtractedMemory(
                summary=topic,
                memory_type="semantic",  # Images are semantic facts
                raw_dialogue=raw_dialogue,
                confidence=1.0,  # High confidence - directly from Claude Vision
                global_safe=False,  # Keep at image's privacy level
            )

            # Generate embedding using text model (voyage-3.5-lite)
            embedding = await self.retriever._embed(memory.summary, input_type="document")

            # Add to memory system with image link
            memory_id = await self.updater._add(
                user_id=user_id,
                memory=memory,
                embedding=embedding,
                privacy_level=privacy_level,
                channel_id=channel_id,
                guild_id=guild_id,
                linked_image_id=observation_id,
            )

            logger.info(f"Created text memory {memory_id} linked to image observation {observation_id}")
            return memory_id

        except Exception as e:
            logger.error(f"Failed to create image text memory: {e}", exc_info=True)
            return None

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

            for memory, privacy_level in extracted_with_privacy:
                logger.info(f"Storing memory: [{privacy_level.value}] {memory.summary[:50]}...")
                await self.updater.update(
                    user_id, memory, privacy_level, channel_id, guild_id
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
