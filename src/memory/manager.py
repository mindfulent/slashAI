"""
Memory Manager

Facade orchestrating memory extraction, retrieval, and update operations
with privacy enforcement.
"""

import json
import logging
from typing import Optional

import asyncpg
import discord
from anthropic import AsyncAnthropic

logger = logging.getLogger("slashAI.memory")

from .config import MemoryConfig
from .extractor import MemoryExtractor
from .privacy import PrivacyLevel, classify_channel_privacy
from .retriever import MemoryRetriever, RetrievedMemory
from .updater import MemoryUpdater


class MemoryManager:
    """Facade for memory operations with privacy enforcement."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        anthropic_client: AsyncAnthropic,
        config: Optional[MemoryConfig] = None,
    ):
        self.config = config or MemoryConfig.from_env()
        self.extractor = MemoryExtractor(anthropic_client)
        self.retriever = MemoryRetriever(db_pool, self.config)
        self.updater = MemoryUpdater(
            db_pool, self.retriever, anthropic_client, self.config
        )
        self.db = db_pool

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
        logger.info(f"Retrieving memories for user={user_id}, query={query[:50]}...")
        memories = await self.retriever.retrieve(user_id, query, channel)
        logger.info(f"Retrieved {len(memories)} memories")
        for mem in memories:
            logger.debug(f"  - [{mem.memory_type}] {mem.summary[:50]}... (sim={mem.similarity:.3f})")
        return memories

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
        messages = session["messages"] or []
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": assistant_message})

        await self.db.execute(
            """UPDATE memory_sessions SET messages = $1, message_count = message_count + 2,
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
        logger.info(f"Extracting memories from {len(messages)} messages")
        extracted_with_privacy = await self.extractor.extract_with_privacy(
            messages, channel
        )
        logger.info(f"Extracted {len(extracted_with_privacy)} memory topics")

        guild = getattr(channel, "guild", None)
        guild_id = guild.id if guild else None

        for memory, privacy_level in extracted_with_privacy:
            logger.info(f"Storing memory: [{privacy_level.value}] {memory.topic_summary[:50]}...")
            await self.updater.update(
                user_id, memory, privacy_level, channel_id, guild_id
            )

        # Reset session after extraction
        await self.db.execute(
            """UPDATE memory_sessions SET extracted_at = NOW(), messages = '[]'::jsonb,
               message_count = 0 WHERE user_id = $1 AND channel_id = $2""",
            user_id,
            channel_id,
        )
        logger.info(f"Session reset for user={user_id}, channel={channel_id}")
