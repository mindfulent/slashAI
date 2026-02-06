#!/usr/bin/env python
# slashAI - Discord chatbot with persistent memory
# Copyright (C) 2025 Slashington
# SPDX-License-Identifier: AGPL-3.0-or-later
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
# Commercial licensing: Contact info@slashai.dev

"""
Backfill historical reactions from Discord messages.

This script fetches reactions from Discord message history and stores
them in the message_reactions table for memory integration.

Phases:
  1. slashAI's own messages (highest priority - direct engagement)
  2. Threads where slashAI participated (community discussions)
  3. All public channel messages (full history, optional)

Usage:
  python scripts/backfill_reactions.py --guild 123 --phase 1 --dry-run
  python scripts/backfill_reactions.py --guild 123 --phase 1 --apply
  python scripts/backfill_reactions.py --guild 123 --after 2025-01-01

Rate Limits:
  - Discord API: ~50 requests/second
  - Estimated time: 10 minutes per 10,000 messages with 2 reactions each
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
import discord
from dotenv import load_dotenv

from memory.reactions import ReactionStore, get_emoji_dimensions

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


class ReactionBackfill:
    """Backfills historical reactions from Discord."""

    def __init__(self, bot: discord.Client, db_pool: asyncpg.Pool):
        self.bot = bot
        self.store = ReactionStore(db_pool)
        self.stats = {
            "channels_processed": 0,
            "messages_scanned": 0,
            "reactions_found": 0,
            "reactions_stored": 0,
            "reactions_skipped": 0,
            "errors": 0,
        }

    async def run(
        self,
        guild_id: int,
        phase: int = 1,
        after_date: datetime = None,
        dry_run: bool = True,
    ):
        """
        Run the backfill operation.

        Args:
            guild_id: Discord guild ID to backfill
            phase: 1=bot messages, 2=threads, 3=all
            after_date: Only process messages after this date
            dry_run: If True, don't actually store anything
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            logger.error(f"Guild {guild_id} not found")
            return

        logger.info(f"Starting backfill for guild: {guild.name}")
        logger.info(f"Phase: {phase}, Dry run: {dry_run}")
        if after_date:
            logger.info(f"After date: {after_date}")

        # Get text channels
        channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]
        logger.info(f"Found {len(channels)} text channels")

        for channel in channels:
            try:
                await self._process_channel(channel, phase, after_date, dry_run)
            except discord.Forbidden:
                logger.warning(f"No access to channel: {channel.name}")
            except Exception as e:
                logger.error(f"Error processing {channel.name}: {e}")
                self.stats["errors"] += 1

        self._print_stats()

    async def _process_channel(
        self,
        channel: discord.TextChannel,
        phase: int,
        after_date: datetime,
        dry_run: bool,
    ):
        """Process a single channel."""
        logger.info(f"Processing channel: {channel.name}")
        self.stats["channels_processed"] += 1

        async for message in channel.history(limit=None, after=after_date):
            self.stats["messages_scanned"] += 1

            # Phase filtering
            if phase == 1:
                # Only bot's messages
                if message.author.id != self.bot.user.id:
                    continue
            elif phase == 2:
                # Only threads (skip for now, handled separately)
                continue

            # Process reactions on this message
            for reaction in message.reactions:
                # Get users who reacted
                try:
                    users = [u async for u in reaction.users()]
                except discord.HTTPException:
                    continue

                for user in users:
                    if user.bot:
                        continue

                    self.stats["reactions_found"] += 1

                    # Skip custom emoji
                    if reaction.emoji if isinstance(reaction.emoji, str) else hasattr(reaction.emoji, 'id'):
                        if hasattr(reaction.emoji, 'id'):
                            self.stats["reactions_skipped"] += 1
                            continue

                    emoji_str = str(reaction.emoji)
                    dimensions = get_emoji_dimensions(emoji_str)

                    if not dry_run:
                        result = await self.store.store_reaction(
                            message_id=message.id,
                            channel_id=channel.id,
                            guild_id=channel.guild.id,
                            message_author_id=message.author.id,
                            reactor_id=user.id,
                            emoji=emoji_str,
                            dimensions=dimensions,
                        )
                        if result:
                            self.stats["reactions_stored"] += 1
                    else:
                        logger.debug(
                            f"Would store: {emoji_str} by {user.name} on msg {message.id}"
                        )
                        self.stats["reactions_stored"] += 1

            # Progress logging
            if self.stats["messages_scanned"] % 100 == 0:
                logger.info(
                    f"Progress: {self.stats['messages_scanned']} messages, "
                    f"{self.stats['reactions_found']} reactions"
                )

    def _print_stats(self):
        """Print final statistics."""
        logger.info("=" * 50)
        logger.info("Backfill Complete")
        logger.info("=" * 50)
        for key, value in self.stats.items():
            logger.info(f"  {key}: {value}")


class BackfillBot(discord.Client):
    """Minimal Discord client for backfill operations."""

    def __init__(self, guild_id: int, phase: int, after_date: datetime, dry_run: bool):
        intents = discord.Intents.default()
        intents.messages = True
        intents.reactions = True
        intents.guilds = True
        intents.message_content = True
        super().__init__(intents=intents)

        self.guild_id = guild_id
        self.phase = phase
        self.after_date = after_date
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
            backfill = ReactionBackfill(self, self.db_pool)
            await backfill.run(
                guild_id=self.guild_id,
                phase=self.phase,
                after_date=self.after_date,
                dry_run=self.dry_run,
            )
        finally:
            if self.db_pool:
                await self.db_pool.close()
            await self.close()


def parse_date(date_str: str) -> datetime:
    """Parse date string to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical reactions from Discord"
    )
    parser.add_argument("--guild", type=int, required=True, help="Discord guild ID")
    parser.add_argument(
        "--phase",
        type=int,
        default=1,
        choices=[1, 2, 3],
        help="1=bot messages, 2=threads, 3=all (default: 1)",
    )
    parser.add_argument(
        "--after",
        type=str,
        default=None,
        help="Only process messages after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview without storing (default: True)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually store reactions (disables dry-run)",
    )

    args = parser.parse_args()

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set")
        return

    after_date = parse_date(args.after) if args.after else None
    dry_run = not args.apply

    bot = BackfillBot(
        guild_id=args.guild,
        phase=args.phase,
        after_date=after_date,
        dry_run=dry_run,
    )

    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
