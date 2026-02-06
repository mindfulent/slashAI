# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Database operations for reaction storage.

Handles CRUD operations for the message_reactions and memory_message_links tables.

Part of v0.12.0 - Reaction-Based Memory Signals.
"""

import logging
from datetime import datetime
from typing import Optional

import asyncpg

from .dimensions import EmojiDimensions

logger = logging.getLogger(__name__)


class ReactionStore:
    """Database operations for reaction storage."""

    def __init__(self, db_pool: asyncpg.Pool):
        """
        Initialize the reaction store.

        Args:
            db_pool: AsyncPG connection pool
        """
        self.db = db_pool

    async def store_reaction(
        self,
        message_id: int,
        channel_id: int,
        guild_id: Optional[int],
        message_author_id: int,
        reactor_id: int,
        emoji: str,
        dimensions: EmojiDimensions,
        emoji_is_custom: bool = False,
    ) -> Optional[int]:
        """
        Store a reaction in the database.

        Uses INSERT ... ON CONFLICT to handle re-reactions (clear removed_at).

        Args:
            message_id: Discord message ID
            channel_id: Discord channel ID
            guild_id: Discord guild ID (None for DMs)
            message_author_id: User ID of message author
            reactor_id: User ID of person who reacted
            emoji: Emoji string (unicode or custom name)
            dimensions: Emoji dimension mapping
            emoji_is_custom: True if this is a custom server emoji

        Returns:
            Reaction ID or None on failure
        """
        try:
            result = await self.db.fetchrow(
                """
                INSERT INTO message_reactions (
                    message_id, channel_id, guild_id, message_author_id,
                    reactor_id, emoji, emoji_is_custom,
                    sentiment, intensity, intent, relevance, context_dependent,
                    reacted_at, removed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW(), NULL)
                ON CONFLICT (message_id, reactor_id, emoji)
                DO UPDATE SET
                    removed_at = NULL,
                    reacted_at = NOW()
                RETURNING id
                """,
                message_id,
                channel_id,
                guild_id,
                message_author_id,
                reactor_id,
                emoji,
                emoji_is_custom,
                dimensions.get("sentiment"),
                dimensions.get("intensity"),
                dimensions.get("intent"),
                dimensions.get("relevance"),
                dimensions.get("context_dependent", False),
            )
            return result["id"] if result else None

        except Exception as e:
            logger.error(f"Error storing reaction: {e}", exc_info=True)
            return None

    async def remove_reaction(
        self,
        message_id: int,
        reactor_id: int,
        emoji: str,
    ) -> bool:
        """
        Mark a reaction as removed (soft delete).

        Args:
            message_id: Discord message ID
            reactor_id: User ID of person who reacted
            emoji: Emoji string

        Returns:
            True if reaction was found and updated
        """
        try:
            result = await self.db.execute(
                """
                UPDATE message_reactions
                SET removed_at = NOW()
                WHERE message_id = $1 AND reactor_id = $2 AND emoji = $3
                    AND removed_at IS NULL
                """,
                message_id,
                reactor_id,
                emoji,
            )
            return result == "UPDATE 1"

        except Exception as e:
            logger.error(f"Error removing reaction: {e}", exc_info=True)
            return False

    async def get_reactions_for_message(
        self,
        message_id: int,
        active_only: bool = True,
    ) -> list[dict]:
        """
        Get all reactions for a message.

        Args:
            message_id: Discord message ID
            active_only: If True, exclude removed reactions

        Returns:
            List of reaction records
        """
        try:
            query = """
                SELECT id, message_id, channel_id, guild_id, message_author_id,
                       reactor_id, emoji, emoji_is_custom,
                       sentiment, intensity, intent, relevance, context_dependent,
                       reacted_at, removed_at
                FROM message_reactions
                WHERE message_id = $1
            """
            if active_only:
                query += " AND removed_at IS NULL"

            rows = await self.db.fetch(query, message_id)
            return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting reactions for message: {e}", exc_info=True)
            return []

    async def get_reactions_by_reactor(
        self,
        reactor_id: int,
        limit: int = 100,
        active_only: bool = True,
    ) -> list[dict]:
        """
        Get reactions made by a specific user.

        Args:
            reactor_id: Discord user ID
            limit: Maximum number of reactions to return
            active_only: If True, exclude removed reactions

        Returns:
            List of reaction records
        """
        try:
            query = """
                SELECT id, message_id, channel_id, guild_id, message_author_id,
                       reactor_id, emoji, emoji_is_custom,
                       sentiment, intensity, intent, relevance, context_dependent,
                       reacted_at, removed_at
                FROM message_reactions
                WHERE reactor_id = $1
            """
            if active_only:
                query += " AND removed_at IS NULL"
            query += " ORDER BY reacted_at DESC LIMIT $2"

            rows = await self.db.fetch(query, reactor_id, limit)
            return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting reactions by reactor: {e}", exc_info=True)
            return []

    async def get_reactions_for_author(
        self,
        message_author_id: int,
        limit: int = 100,
        active_only: bool = True,
    ) -> list[dict]:
        """
        Get reactions received on messages by a specific author.

        Args:
            message_author_id: Discord user ID of message authors
            limit: Maximum number of reactions to return
            active_only: If True, exclude removed reactions

        Returns:
            List of reaction records
        """
        try:
            query = """
                SELECT id, message_id, channel_id, guild_id, message_author_id,
                       reactor_id, emoji, emoji_is_custom,
                       sentiment, intensity, intent, relevance, context_dependent,
                       reacted_at, removed_at
                FROM message_reactions
                WHERE message_author_id = $1
            """
            if active_only:
                query += " AND removed_at IS NULL"
            query += " ORDER BY reacted_at DESC LIMIT $2"

            rows = await self.db.fetch(query, message_author_id, limit)
            return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting reactions for author: {e}", exc_info=True)
            return []

    # ===== Memory-Message Link Operations =====

    async def create_memory_link(
        self,
        memory_id: int,
        message_id: int,
        channel_id: int,
        contribution_type: str = "source",
    ) -> Optional[int]:
        """
        Create a link between a memory and its source message.

        Args:
            memory_id: Memory ID
            message_id: Discord message ID
            channel_id: Discord channel ID
            contribution_type: Type of contribution ('source' or 'mentioned')

        Returns:
            Link ID or None on failure
        """
        try:
            result = await self.db.fetchrow(
                """
                INSERT INTO memory_message_links (memory_id, message_id, channel_id, contribution_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (memory_id, message_id) DO NOTHING
                RETURNING id
                """,
                memory_id,
                message_id,
                channel_id,
                contribution_type,
            )
            return result["id"] if result else None

        except Exception as e:
            logger.error(f"Error creating memory link: {e}", exc_info=True)
            return None

    async def get_message_ids_for_memory(self, memory_id: int) -> list[int]:
        """
        Get all message IDs linked to a memory.

        Args:
            memory_id: Memory ID

        Returns:
            List of Discord message IDs
        """
        try:
            rows = await self.db.fetch(
                "SELECT message_id FROM memory_message_links WHERE memory_id = $1",
                memory_id,
            )
            return [row["message_id"] for row in rows]

        except Exception as e:
            logger.error(f"Error getting message IDs for memory: {e}", exc_info=True)
            return []

    async def get_memory_ids_for_message(self, message_id: int) -> list[int]:
        """
        Get all memory IDs linked to a message.

        Args:
            message_id: Discord message ID

        Returns:
            List of memory IDs
        """
        try:
            rows = await self.db.fetch(
                "SELECT memory_id FROM memory_message_links WHERE message_id = $1",
                message_id,
            )
            return [row["memory_id"] for row in rows]

        except Exception as e:
            logger.error(f"Error getting memory IDs for message: {e}", exc_info=True)
            return []

    async def get_reactions_for_memory(
        self,
        memory_id: int,
        active_only: bool = True,
    ) -> list[dict]:
        """
        Get all reactions on messages linked to a memory.

        Args:
            memory_id: Memory ID
            active_only: If True, exclude removed reactions

        Returns:
            List of reaction records
        """
        try:
            query = """
                SELECT r.id, r.message_id, r.channel_id, r.guild_id, r.message_author_id,
                       r.reactor_id, r.emoji, r.emoji_is_custom,
                       r.sentiment, r.intensity, r.intent, r.relevance, r.context_dependent,
                       r.reacted_at, r.removed_at
                FROM message_reactions r
                JOIN memory_message_links l ON r.message_id = l.message_id
                WHERE l.memory_id = $1
            """
            if active_only:
                query += " AND r.removed_at IS NULL"

            rows = await self.db.fetch(query, memory_id)
            return [dict(row) for row in rows]

        except Exception as e:
            logger.error(f"Error getting reactions for memory: {e}", exc_info=True)
            return []

    # ===== Statistics =====

    async def get_reaction_stats(self, guild_id: Optional[int] = None) -> dict:
        """
        Get aggregate reaction statistics.

        Args:
            guild_id: Filter to specific guild (None for all)

        Returns:
            Dictionary with statistics
        """
        try:
            if guild_id:
                total_reactions = await self.db.fetchval(
                    "SELECT COUNT(*) FROM message_reactions WHERE guild_id = $1",
                    guild_id,
                )
                active_reactions = await self.db.fetchval(
                    "SELECT COUNT(*) FROM message_reactions WHERE guild_id = $1 AND removed_at IS NULL",
                    guild_id,
                )
                unique_reactors = await self.db.fetchval(
                    "SELECT COUNT(DISTINCT reactor_id) FROM message_reactions WHERE guild_id = $1 AND removed_at IS NULL",
                    guild_id,
                )
                unique_messages = await self.db.fetchval(
                    "SELECT COUNT(DISTINCT message_id) FROM message_reactions WHERE guild_id = $1 AND removed_at IS NULL",
                    guild_id,
                )
            else:
                total_reactions = await self.db.fetchval(
                    "SELECT COUNT(*) FROM message_reactions"
                )
                active_reactions = await self.db.fetchval(
                    "SELECT COUNT(*) FROM message_reactions WHERE removed_at IS NULL"
                )
                unique_reactors = await self.db.fetchval(
                    "SELECT COUNT(DISTINCT reactor_id) FROM message_reactions WHERE removed_at IS NULL"
                )
                unique_messages = await self.db.fetchval(
                    "SELECT COUNT(DISTINCT message_id) FROM message_reactions WHERE removed_at IS NULL"
                )

            # Top emoji
            top_emoji_rows = await self.db.fetch(
                """
                SELECT emoji, COUNT(*) as count
                FROM message_reactions
                WHERE removed_at IS NULL
                GROUP BY emoji
                ORDER BY count DESC
                LIMIT 10
                """
            )
            top_emoji = [{"emoji": row["emoji"], "count": row["count"]} for row in top_emoji_rows]

            # Sentiment distribution
            sentiment_stats = await self.db.fetchrow(
                """
                SELECT
                    AVG(sentiment) as avg_sentiment,
                    COUNT(*) FILTER (WHERE sentiment > 0.5) as positive_count,
                    COUNT(*) FILTER (WHERE sentiment < -0.5) as negative_count,
                    COUNT(*) FILTER (WHERE sentiment BETWEEN -0.5 AND 0.5) as neutral_count
                FROM message_reactions
                WHERE removed_at IS NULL AND sentiment IS NOT NULL
                """
            )

            return {
                "total_reactions": total_reactions or 0,
                "active_reactions": active_reactions or 0,
                "unique_reactors": unique_reactors or 0,
                "unique_messages": unique_messages or 0,
                "top_emoji": top_emoji,
                "avg_sentiment": float(sentiment_stats["avg_sentiment"] or 0),
                "positive_count": sentiment_stats["positive_count"] or 0,
                "negative_count": sentiment_stats["negative_count"] or 0,
                "neutral_count": sentiment_stats["neutral_count"] or 0,
            }

        except Exception as e:
            logger.error(f"Error getting reaction stats: {e}", exc_info=True)
            return {
                "total_reactions": 0,
                "active_reactions": 0,
                "unique_reactors": 0,
                "unique_messages": 0,
                "top_emoji": [],
                "avg_sentiment": 0,
                "positive_count": 0,
                "negative_count": 0,
                "neutral_count": 0,
            }

    async def get_memory_link_stats(self) -> dict:
        """Get statistics about memory-message links."""
        try:
            total_links = await self.db.fetchval("SELECT COUNT(*) FROM memory_message_links")
            linked_memories = await self.db.fetchval(
                "SELECT COUNT(DISTINCT memory_id) FROM memory_message_links"
            )
            linked_messages = await self.db.fetchval(
                "SELECT COUNT(DISTINCT message_id) FROM memory_message_links"
            )

            return {
                "total_links": total_links or 0,
                "linked_memories": linked_memories or 0,
                "linked_messages": linked_messages or 0,
            }

        except Exception as e:
            logger.error(f"Error getting memory link stats: {e}", exc_info=True)
            return {"total_links": 0, "linked_memories": 0, "linked_messages": 0}
