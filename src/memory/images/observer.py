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
Image Observer - Main entry point for image processing pipeline.

Orchestrates:
1. Content moderation (must pass before any storage)
2. Image analysis (Claude vision + embeddings)
3. Storage (DO Spaces)
4. Clustering (build grouping)
"""

import gc
import logging
from datetime import datetime
from typing import Optional

import asyncpg
import discord
from anthropic import AsyncAnthropic

from ..privacy import PrivacyLevel, classify_channel_privacy
from .analyzer import ImageAnalyzer, ImageAnalysisConfig, ModerationResult
from .clusterer import BuildClusterer, ClusterConfig
from .narrator import BuildNarrator
from .storage import ImageStorage


# Supported image formats
logger = logging.getLogger("slashAI.images")

SUPPORTED_FORMATS = {"png", "jpg", "jpeg", "gif", "webp"}

# MIME type mapping
MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


class ImageObserver:
    """
    Main entry point for processing shared images.

    Handles the full pipeline: moderation -> analysis -> storage -> clustering.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        anthropic_client: AsyncAnthropic,
        storage: ImageStorage,
        analyzer: Optional[ImageAnalyzer] = None,
        clusterer: Optional[BuildClusterer] = None,
        narrator: Optional[BuildNarrator] = None,
        moderation_enabled: bool = True,
        memory_manager=None,  # Optional MemoryManager for text-image bridge
    ):
        self.db = db_pool
        self.anthropic = anthropic_client
        self.storage = storage
        self.moderation_enabled = moderation_enabled
        self.memory_manager = memory_manager

        # Initialize components
        self.analyzer = analyzer or ImageAnalyzer(anthropic_client)
        self.clusterer = clusterer or BuildClusterer(db_pool)
        self.narrator = narrator or BuildNarrator(db_pool, anthropic_client)

        # Cache for mod channel lookups
        self._mod_channels: dict[int, Optional[int]] = {}

    async def handle_image(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
        bot: Optional[discord.Client] = None,
    ) -> Optional[int]:
        """
        Process an image attachment.

        Args:
            message: Discord message containing the image
            attachment: The image attachment
            bot: Discord bot client (for moderation notifications)

        Returns:
            observation_id if stored, None if rejected/moderated
        """
        logger.info(f"[OBSERVER] Starting handle_image for {attachment.filename}")
        
        # Validate format
        if not self._is_supported_image(attachment.filename):
            logger.warning(f"[OBSERVER] Unsupported format: {attachment.filename}")
            return None

        # Download image
        logger.info(f"[OBSERVER] Downloading image from Discord (size={attachment.size} bytes)...")
        try:
            image_bytes = await attachment.read()
            logger.info(f"[OBSERVER] Downloaded {len(image_bytes)} bytes successfully")
        except discord.HTTPException as e:
            logger.error(f"[OBSERVER] Failed to download image: {e}")
            return None

        media_type = self._get_media_type(attachment.filename)
        logger.info(f"[OBSERVER] Media type: {media_type}")

        # STEP 1: Content moderation (MUST happen first)
        if self.moderation_enabled:
            logger.info(f"[OBSERVER] Step 1: Running content moderation...")
            moderation = await self.analyzer.moderate(image_bytes, media_type)
            logger.info(f"[OBSERVER] Moderation result: safe={moderation.is_safe}, confidence={moderation.confidence}, type={moderation.violation_type}")

            if not moderation.is_safe:
                if moderation.confidence >= 0.7:
                    # High confidence violation - active moderation
                    await self._handle_violation(
                        message, moderation, delete_message=True, bot=bot
                    )
                    return None
                elif moderation.confidence >= 0.5:
                    # Uncertain - flag for review but still process
                    await self._flag_for_review(message, moderation, bot=bot)
                    # Continue processing...

        # STEP 2: Check for duplicate
        file_hash = None  # Will be set by analysis

        # STEP 3: Full analysis (description, tags, embedding)
        logger.info(f"[OBSERVER] Step 3: Running full analysis (Claude Vision + Voyage embedding)...")
        analysis = await self.analyzer.analyze(image_bytes, media_type)
        file_hash = analysis.file_hash
        logger.info(f"[OBSERVER] Analysis complete: type={analysis.observation_type}, tags={analysis.tags[:3] if analysis.tags else []}, embedding_dims={len(analysis.embedding)}")

        # Check for existing observation with same hash
        existing = await self._check_duplicate(file_hash, message.author.id)
        if existing:
            return existing

        # STEP 4: Upload to storage
        logger.info(f"[OBSERVER] Step 4: Uploading to DO Spaces...")
        storage_key, storage_url = await self.storage.upload(
            image_bytes, message.author.id, file_hash, media_type
        )
        logger.info(f"[OBSERVER] Uploaded: key={storage_key}")

        # STEP 5: Get privacy level
        privacy_level = await classify_channel_privacy(message.channel)
        guild_id = message.guild.id if message.guild else None

        # STEP 6: Insert observation
        logger.info(f"[OBSERVER] Step 6: Inserting observation into database...")
        observation_id = await self._insert_observation(
            user_id=message.author.id,
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=guild_id,
            storage_key=storage_key,
            storage_url=storage_url,
            original_url=attachment.url,
            file_hash=file_hash,
            file_size_bytes=len(image_bytes),
            dimensions=f"{attachment.width}x{attachment.height}"
            if attachment.width and attachment.height
            else None,
            description=analysis.description,
            summary=analysis.summary,
            tags=analysis.tags,
            detected_elements=analysis.detected_elements,
            embedding=analysis.embedding,
            observation_type=analysis.observation_type,
            privacy_level=privacy_level,
            accompanying_text=message.content if message.content else None,
            captured_at=message.created_at,
        )

        logger.info(f"[OBSERVER] Inserted observation_id={observation_id}")
        
        # STEP 7: Assign to cluster
        logger.info(f"[OBSERVER] Step 7: Assigning to cluster...")
        await self.clusterer.assign_to_cluster(
            user_id=message.author.id,
            observation_id=observation_id,
            embedding=analysis.embedding,
            observation_type=analysis.observation_type,
            tags=analysis.tags,
            privacy_level=privacy_level.value,
            guild_id=guild_id,
        )

        logger.info(f"[OBSERVER] Complete! observation_id={observation_id}")

        # STEP 8: Create text memory for text-image bridging (if memory manager available)
        if self.memory_manager:
            logger.info(f"[OBSERVER] Step 8: Creating text memory for image observation...")
            try:
                await self.memory_manager.create_image_text_memory(
                    user_id=message.author.id,
                    observation_id=observation_id,
                    description=analysis.description,
                    summary=analysis.summary,
                    tags=analysis.tags,
                    accompanying_text=message.content if message.content else None,
                    privacy_level=privacy_level,
                    channel_id=message.channel.id,
                    guild_id=guild_id,
                )
                logger.info(f"[OBSERVER] Text memory created successfully for observation {observation_id}")
            except Exception as e:
                logger.error(f"[OBSERVER] Failed to create text memory: {e}", exc_info=True)
                # Continue even if text memory creation fails - image is still stored

        # Free memory on constrained workers
        del image_bytes
        del analysis
        gc.collect()

        return observation_id

    async def _check_duplicate(
        self,
        file_hash: str,
        user_id: int,
    ) -> Optional[int]:
        """Check if image already exists for this user."""
        row = await self.db.fetchrow(
            "SELECT id FROM image_observations WHERE file_hash = $1 AND user_id = $2",
            file_hash,
            user_id,
        )
        return row["id"] if row else None

    async def _insert_observation(
        self,
        user_id: int,
        message_id: int,
        channel_id: int,
        guild_id: Optional[int],
        storage_key: str,
        storage_url: str,
        original_url: Optional[str],
        file_hash: str,
        file_size_bytes: Optional[int],
        dimensions: Optional[str],
        description: str,
        summary: str,
        tags: list[str],
        detected_elements: dict,
        embedding: list[float],
        observation_type: str,
        privacy_level: PrivacyLevel,
        accompanying_text: Optional[str],
        captured_at: datetime,
    ) -> int:
        """Insert a new image observation record."""
        import json

        # Convert embedding list to pgvector string format
        embedding_str = '[' + ','.join(str(x) for x in embedding) + ']'
        
        row = await self.db.fetchrow(
            """
            INSERT INTO image_observations (
                user_id, message_id, channel_id, guild_id,
                storage_key, storage_url, original_url, file_hash,
                file_size_bytes, dimensions,
                description, summary, tags, detected_elements,
                embedding, observation_type, privacy_level,
                accompanying_text, captured_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19
            )
            RETURNING id
            """,
            user_id,
            message_id,
            channel_id,
            guild_id,
            storage_key,
            storage_url,
            original_url,
            file_hash,
            file_size_bytes,
            dimensions,
            description,
            summary,
            tags,
            json.dumps(detected_elements),
            embedding_str,
            observation_type,
            privacy_level.value,
            accompanying_text,
            captured_at,
        )

        return row["id"]

    async def _handle_violation(
        self,
        message: discord.Message,
        moderation: ModerationResult,
        delete_message: bool,
        bot: Optional[discord.Client] = None,
    ) -> None:
        """Handle a content policy violation."""
        # Log to database (text description only, NO image)
        await self.db.execute(
            """
            INSERT INTO image_moderation_log (
                user_id, message_id, channel_id, guild_id,
                violation_type, violation_description, confidence,
                message_deleted, user_warned, admin_notified
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            message.author.id,
            message.id,
            message.channel.id,
            message.guild.id if message.guild else None,
            moderation.violation_type or "other",
            moderation.description,
            moderation.confidence,
            delete_message,
            True,
            True,
        )

        # Delete the message
        if delete_message:
            try:
                await message.delete()
            except discord.Forbidden:
                pass  # Bot lacks permissions

        # Warn the user via DM
        try:
            await message.author.send(
                f"**Content Warning**\n\n"
                f"An image you shared was flagged and removed for potentially violating "
                f"community guidelines ({moderation.violation_type or 'policy violation'}).\n\n"
                f"If you believe this was a mistake, please contact a moderator."
            )
        except discord.Forbidden:
            pass  # User has DMs disabled

        # Notify moderators
        if bot and message.guild:
            await self._notify_moderators(message, moderation, bot)

    async def _flag_for_review(
        self,
        message: discord.Message,
        moderation: ModerationResult,
        bot: Optional[discord.Client] = None,
    ) -> None:
        """Flag uncertain content for human review without blocking."""
        # Log to database
        await self.db.execute(
            """
            INSERT INTO image_moderation_log (
                user_id, message_id, channel_id, guild_id,
                violation_type, violation_description, confidence,
                message_deleted, user_warned, admin_notified
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            message.author.id,
            message.id,
            message.channel.id,
            message.guild.id if message.guild else None,
            moderation.violation_type or "flagged_for_review",
            f"[REVIEW NEEDED] {moderation.description}",
            moderation.confidence,
            False,
            False,
            True,
        )

        # Notify moderators
        if bot and message.guild:
            await self._notify_moderators(message, moderation, bot, is_review=True)

    async def _notify_moderators(
        self,
        message: discord.Message,
        moderation: ModerationResult,
        bot: discord.Client,
        is_review: bool = False,
    ) -> None:
        """Send notification to moderator channel."""
        if not message.guild:
            return

        mod_channel_id = await self._get_mod_channel(message.guild.id)
        if not mod_channel_id:
            return

        mod_channel = bot.get_channel(mod_channel_id)
        if not mod_channel or not isinstance(mod_channel, discord.TextChannel):
            return

        title = (
            "Image Flagged for Review" if is_review else "Image Moderation Alert"
        )
        color = discord.Color.orange() if is_review else discord.Color.red()

        embed = discord.Embed(
            title=f"{'🔍' if is_review else '🚨'} {title}",
            color=color,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(
            name="User", value=f"{message.author} ({message.author.id})", inline=True
        )
        embed.add_field(
            name="Channel", value=f"<#{message.channel.id}>", inline=True
        )
        embed.add_field(
            name="Violation Type",
            value=moderation.violation_type or "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Confidence", value=f"{moderation.confidence:.0%}", inline=True
        )
        embed.add_field(
            name="Description", value=moderation.description, inline=False
        )
        embed.set_footer(text="Image was NOT stored. This is a text-only log.")

        try:
            await mod_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _get_mod_channel(self, guild_id: int) -> Optional[int]:
        """Get the moderation channel ID for a guild."""
        # Check cache
        if guild_id in self._mod_channels:
            return self._mod_channels[guild_id]

        # For now, use environment variable
        # In production, this would be per-guild configuration
        import os

        mod_channel_str = os.getenv("MOD_CHANNEL_ID")
        mod_channel_id = int(mod_channel_str) if mod_channel_str else None

        self._mod_channels[guild_id] = mod_channel_id
        return mod_channel_id

    def _is_supported_image(self, filename: str) -> bool:
        """Check if file extension is a supported image format."""
        if not filename:
            return False
        ext = filename.rsplit(".", 1)[-1].lower()
        return ext in SUPPORTED_FORMATS

    def _get_media_type(self, filename: str) -> str:
        """Get MIME type from filename."""
        ext = filename.rsplit(".", 1)[-1].lower()
        return MIME_TYPES.get(ext, "image/png")

    async def get_observation_by_message(
        self,
        message_id: int,
    ) -> Optional[dict]:
        """Get observation by Discord message ID."""
        row = await self.db.fetchrow(
            "SELECT * FROM image_observations WHERE message_id = $1",
            message_id,
        )
        return dict(row) if row else None

    async def get_recent_observations(
        self,
        user_id: int,
        privacy_level: str,
        guild_id: Optional[int],
        limit: int = 5,
    ) -> list[dict]:
        """Get recent observations for a user with privacy filtering."""
        if privacy_level == "dm":
            sql = """
                SELECT * FROM image_observations
                WHERE user_id = $1
                ORDER BY captured_at DESC
                LIMIT $2
            """
            rows = await self.db.fetch(sql, user_id, limit)
        elif privacy_level == "channel_restricted":
            sql = """
                SELECT * FROM image_observations
                WHERE user_id = $1
                  AND (
                    privacy_level IN ('global', 'guild_public')
                    OR (privacy_level = 'channel_restricted' AND guild_id = $3)
                  )
                ORDER BY captured_at DESC
                LIMIT $2
            """
            rows = await self.db.fetch(sql, user_id, limit, guild_id)
        else:
            sql = """
                SELECT * FROM image_observations
                WHERE user_id = $1
                  AND privacy_level IN ('global', 'guild_public')
                  AND guild_id = $3
                ORDER BY captured_at DESC
                LIMIT $2
            """
            rows = await self.db.fetch(sql, user_id, limit, guild_id)

        return [dict(r) for r in rows]
