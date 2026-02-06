#!/usr/bin/env python
# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Backfill community observations for existing reacted messages.

This script creates "community_observation" memories for messages that:
1. Have reactions
2. Don't already have memory links
3. Are in guild channels (not DMs)

Usage:
  python scripts/backfill_community_observations.py --guild 123 --dry-run
  python scripts/backfill_community_observations.py --guild 123 --apply
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
import discord
import voyageai
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


class CommunityObservationBackfill:
    """Backfills community observations for reacted messages."""

    def __init__(self, bot: discord.Client, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool
        # Explicitly pass API key (env var may not propagate to voyageai)
        self.voyage = voyageai.AsyncClient(api_key=os.getenv("VOYAGE_API_KEY"))
        self.stats = {
            "messages_found": 0,
            "observations_created": 0,
            "skipped_no_content": 0,
            "skipped_bot": 0,
            "skipped_short": 0,
            "skipped_not_found": 0,
            "errors": 0,
        }

    async def run(self, guild_id: int, dry_run: bool = True):
        """Run the backfill operation."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.error(f"Guild {guild_id} not found")
            return

        logger.info(f"Starting community observation backfill for guild: {guild.name}")
        logger.info(f"Dry run: {dry_run}")

        # Get messages with reactions but no memory links
        rows = await self.db.fetch(
            """
            SELECT DISTINCT
                r.message_id,
                r.channel_id,
                r.guild_id,
                r.message_author_id
            FROM message_reactions r
            LEFT JOIN memory_message_links l ON r.message_id = l.message_id
            WHERE r.removed_at IS NULL
              AND l.id IS NULL
              AND r.guild_id = $1
            ORDER BY r.message_id
            """,
            guild_id,
        )

        self.stats["messages_found"] = len(rows)
        logger.info(f"Found {len(rows)} messages with reactions but no memory links")

        for i, row in enumerate(rows):
            try:
                await self._process_message(row, dry_run)

                if (i + 1) % 25 == 0:
                    logger.info(
                        f"Progress: {i + 1}/{len(rows)} messages, "
                        f"{self.stats['observations_created']} created"
                    )
            except Exception as e:
                logger.error(f"Error processing message {row['message_id']}: {e}")
                self.stats["errors"] += 1

        self._print_stats()

    async def _process_message(self, row: dict, dry_run: bool):
        """Process a single message for community observation."""
        message_id = row["message_id"]
        channel_id = row["channel_id"]

        # Get channel
        channel = self.bot.get_channel(channel_id)
        if not channel:
            self.stats["skipped_not_found"] += 1
            return

        # Fetch message
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            self.stats["skipped_not_found"] += 1
            return
        except discord.Forbidden:
            self.stats["skipped_not_found"] += 1
            return

        # Skip bot messages
        if message.author.bot:
            self.stats["skipped_bot"] += 1
            return

        # Skip empty or very short messages
        if not message.content or len(message.content) < 10:
            self.stats["skipped_short"] += 1
            return

        if dry_run:
            logger.debug(f"Would create observation for: {message.content[:50]}...")
            self.stats["observations_created"] += 1
            return

        # Create observation
        await self._create_observation(
            message_id=message_id,
            channel_id=channel_id,
            guild_id=row["guild_id"],
            author_id=message.author.id,
            content=message.content,
        )

    async def _create_observation(
        self,
        message_id: int,
        channel_id: int,
        guild_id: int,
        author_id: int,
        content: str,
    ):
        """Create a community observation memory."""
        try:
            # Truncate content for summary
            summary = content[:500] if len(content) > 500 else content

            # Try to generate embedding, but don't fail if Voyage API is unavailable
            embedding_str = None
            try:
                result = await self.voyage.embed(
                    [summary], model="voyage-3.5-lite", input_type="document"
                )
                embedding = result.embeddings[0]
                embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            except Exception as e:
                # Skip embedding - memory will still work for reaction aggregation
                if "invalid" not in str(e).lower():
                    logger.warning(f"Could not generate embedding: {e}")

            # Create memory (with or without embedding)
            if embedding_str:
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
                    0.5,
                    guild_id,
                    channel_id,
                    embedding_str,
                )
            else:
                memory_id = await self.db.fetchval(
                    """
                    INSERT INTO memories (
                        user_id, topic_summary, raw_dialogue, memory_type,
                        privacy_level, confidence, origin_guild_id, origin_channel_id
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    author_id,
                    summary,
                    content,
                    "community_observation",
                    "guild_public",
                    0.5,
                    guild_id,
                    channel_id,
                )

            # Create link
            await self.db.execute(
                """
                INSERT INTO memory_message_links (memory_id, message_id, channel_id, contribution_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT DO NOTHING
                """,
                memory_id,
                message_id,
                channel_id,
                "community_observation",
            )

            self.stats["observations_created"] += 1
            logger.debug(f"Created observation {memory_id} for message {message_id}")

        except Exception as e:
            logger.error(f"Error creating observation: {e}")
            self.stats["errors"] += 1

    def _print_stats(self):
        """Print final statistics."""
        logger.info("=" * 50)
        logger.info("Community Observation Backfill Complete")
        logger.info("=" * 50)
        for key, value in self.stats.items():
            if value > 0:
                logger.info(f"  {key}: {value}")


class BackfillBot(discord.Client):
    """Minimal Discord client for backfill operations."""

    def __init__(self, guild_id: int, dry_run: bool):
        intents = discord.Intents.default()
        intents.messages = True
        intents.guilds = True
        intents.message_content = True
        super().__init__(intents=intents)

        self.guild_id = guild_id
        self.dry_run = dry_run
        self.db_pool = None

    async def setup_hook(self):
        """Initialize database connection."""
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.error("DATABASE_URL not set")
            await self.close()
            return

        self.db_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)

    async def on_ready(self):
        """Run backfill when connected."""
        logger.info(f"Connected as {self.user}")

        try:
            backfill = CommunityObservationBackfill(self, self.db_pool)
            await backfill.run(
                guild_id=self.guild_id,
                dry_run=self.dry_run,
            )
        finally:
            if self.db_pool:
                await self.db_pool.close()
            await self.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill community observations for reacted messages"
    )
    parser.add_argument("--guild", type=int, required=True, help="Discord guild ID")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview without creating (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create observations (disables dry-run)",
    )

    args = parser.parse_args()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set")
        return

    dry_run = not args.apply

    bot = BackfillBot(
        guild_id=args.guild,
        dry_run=dry_run,
    )

    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
