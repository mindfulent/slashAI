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
Memory Format Migration Script

Converts existing memory summaries from "User's X" format to pronoun-neutral format.
Uses Claude Haiku for fast, cost-effective reformatting.

Usage:
    # Dry run (default) - shows changes without applying
    python scripts/migrate_memory_format.py

    # Apply changes
    python scripts/migrate_memory_format.py --apply

    # Process specific batch
    python scripts/migrate_memory_format.py --batch-size 50 --offset 0
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

import asyncpg
from anthropic import AsyncAnthropic

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Reformatting prompt
REFORMAT_PROMPT = """Convert this memory summary to pronoun-neutral format.

Rules:
- Remove "User's", "User", "They", "Their" references
- Use "IGN: value" not "User's IGN is value"
- Use "Built X" not "User built X"
- Use "Pronouns: they/them" not "User prefers they/them"
- Use "Timezone: PST" not "User is in PST"
- Use action phrases like "Built", "Works on", "Prefers", "Knows"
- Keep the same factual content, just change the format

Examples:
- "User's IGN is slashdaemon" -> "IGN: slashdaemon"
- "User built an ilmango creeper farm" -> "Built ilmango creeper farm"
- "User prefers they/them pronouns" -> "Pronouns: they/them"
- "User is interested in technical Minecraft" -> "Interested in technical Minecraft"
- "User is in PST timezone" -> "Timezone: PST"
- "User knows Python and JavaScript" -> "Knows Python and JavaScript"

If the summary is ALREADY in pronoun-neutral format, return it unchanged.

Convert (respond with ONLY the converted text, nothing else):
"{summary}"
"""


async def reformat_summary(client: AsyncAnthropic, summary: str) -> str:
    """Use Claude Haiku to reformat a single summary."""
    # Skip if already looks pronoun-neutral
    if not any(
        pattern in summary.lower()
        for pattern in ["user's", "user is", "user has", "user built", "user knows", "their ", "they "]
    ):
        # Might already be reformatted
        return summary

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": REFORMAT_PROMPT.format(summary=summary)}],
    )
    return response.content[0].text.strip().strip('"')


async def fetch_memories(
    conn: asyncpg.Connection, batch_size: int, offset: int
) -> list[dict]:
    """Fetch a batch of memories from the database."""
    return await conn.fetch(
        """
        SELECT id, topic_summary, user_id
        FROM memories
        ORDER BY id
        LIMIT $1 OFFSET $2
        """,
        batch_size,
        offset,
    )


async def update_memory(conn: asyncpg.Connection, memory_id: int, new_summary: str):
    """Update a memory's summary in the database."""
    await conn.execute(
        "UPDATE memories SET topic_summary = $1 WHERE id = $2",
        new_summary,
        memory_id,
    )


async def migrate_all_memories(
    db_url: str,
    anthropic_key: str,
    dry_run: bool = True,
    batch_size: int = 100,
    offset: int = 0,
):
    """
    Migrate all memories to pronoun-neutral format.

    Args:
        db_url: PostgreSQL connection string
        anthropic_key: Anthropic API key
        dry_run: If True, print changes without applying them
        batch_size: Number of memories to process at a time
        offset: Starting offset for pagination
    """
    client = AsyncAnthropic(api_key=anthropic_key)
    conn = await asyncpg.connect(db_url)

    try:
        # Get total count
        total = await conn.fetchval("SELECT COUNT(*) FROM memories")
        logger.info(f"Total memories in database: {total}")

        if dry_run:
            logger.info("DRY RUN MODE - No changes will be applied")

        processed = 0
        changed = 0
        unchanged = 0
        errors = 0
        current_offset = offset

        while True:
            memories = await fetch_memories(conn, batch_size, current_offset)
            if not memories:
                break

            for mem in memories:
                memory_id = mem["id"]
                old_summary = mem["topic_summary"]
                user_id = mem["user_id"]

                try:
                    new_summary = await reformat_summary(client, old_summary)

                    if new_summary != old_summary:
                        changed += 1
                        if dry_run:
                            logger.info(f"[WOULD CHANGE] Memory {memory_id} (user {user_id}):")
                            logger.info(f"  OLD: {old_summary}")
                            logger.info(f"  NEW: {new_summary}")
                        else:
                            await update_memory(conn, memory_id, new_summary)
                            logger.info(f"[UPDATED] Memory {memory_id}: {old_summary[:50]}... -> {new_summary[:50]}...")
                    else:
                        unchanged += 1
                        logger.debug(f"[UNCHANGED] Memory {memory_id}: {old_summary[:50]}...")

                except Exception as e:
                    errors += 1
                    logger.error(f"[ERROR] Memory {memory_id}: {e}")

                processed += 1

                # Rate limiting - be gentle with Anthropic API
                if processed % 10 == 0:
                    await asyncio.sleep(0.5)

            current_offset += batch_size
            logger.info(f"Progress: {processed}/{total} ({processed*100//total}%)")

        # Summary
        logger.info("=" * 60)
        logger.info("MIGRATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total processed: {processed}")
        logger.info(f"Changed: {changed}")
        logger.info(f"Unchanged: {unchanged}")
        logger.info(f"Errors: {errors}")
        if dry_run:
            logger.info("(Dry run - no changes were applied)")
            logger.info("Run with --apply to apply changes")

    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate memory summaries to pronoun-neutral format"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of memories per batch (default: 100)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Starting offset for pagination (default: 0)",
    )
    args = parser.parse_args()

    # Get environment variables
    db_url = os.environ.get("DATABASE_URL")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if not db_url:
        logger.error("DATABASE_URL environment variable required")
        sys.exit(1)
    if not anthropic_key:
        logger.error("ANTHROPIC_API_KEY environment variable required")
        sys.exit(1)

    asyncio.run(
        migrate_all_memories(
            db_url=db_url,
            anthropic_key=anthropic_key,
            dry_run=not args.apply,
            batch_size=args.batch_size,
            offset=args.offset,
        )
    )


if __name__ == "__main__":
    main()
