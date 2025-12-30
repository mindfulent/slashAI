"""
Memory Inspector CLI

Debug tool for inspecting and querying the memory system.

Usage:
    # List all memories for a user
    python scripts/memory_inspector.py list --user-id 123456789

    # Show memories with full details
    python scripts/memory_inspector.py list --user-id 123456789 --verbose

    # Filter by privacy level
    python scripts/memory_inspector.py list --user-id 123456789 --privacy guild_public

    # Show memory statistics
    python scripts/memory_inspector.py stats

    # Inspect a specific memory
    python scripts/memory_inspector.py inspect --memory-id 42

    # Search memories by content
    python scripts/memory_inspector.py search --query "creeper farm"

    # Export memories to JSON
    python scripts/memory_inspector.py export --user-id 123456789 --output memories.json

    # Export ALL memories (for backup before migration)
    python scripts/memory_inspector.py export --all --output backups/memories_backup.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import asyncpg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)


def format_datetime(dt: datetime) -> str:
    """Format datetime for display."""
    if dt is None:
        return "Never"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def list_memories(
    conn: asyncpg.Connection,
    user_id: int = None,
    privacy_level: str = None,
    guild_id: int = None,
    verbose: bool = False,
    limit: int = 50,
):
    """List memories with optional filters."""
    conditions = []
    params = []
    param_idx = 1

    if user_id:
        conditions.append(f"user_id = ${param_idx}")
        params.append(user_id)
        param_idx += 1

    if privacy_level:
        conditions.append(f"privacy_level = ${param_idx}")
        params.append(privacy_level)
        param_idx += 1

    if guild_id:
        conditions.append(f"origin_guild_id = ${param_idx}")
        params.append(guild_id)
        param_idx += 1

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    query = f"""
        SELECT id, user_id, topic_summary, memory_type, privacy_level,
               confidence, origin_guild_id, origin_channel_id,
               created_at, updated_at, last_accessed_at
        FROM memories
        WHERE {where_clause}
        ORDER BY updated_at DESC
        LIMIT ${param_idx}
    """

    rows = await conn.fetch(query, *params)

    if not rows:
        logger.info("No memories found matching the criteria.")
        return

    logger.info(f"\n{'='*80}")
    logger.info(f"Found {len(rows)} memories")
    logger.info(f"{'='*80}\n")

    for row in rows:
        logger.info(f"[{row['id']}] {row['memory_type'].upper()} | {row['privacy_level']}")
        logger.info(f"    User: {row['user_id']}")
        logger.info(f"    Summary: {truncate(row['topic_summary'], 70)}")

        if verbose:
            logger.info(f"    Confidence: {row['confidence']:.2f}")
            logger.info(f"    Guild: {row['origin_guild_id']} | Channel: {row['origin_channel_id']}")
            logger.info(f"    Created: {format_datetime(row['created_at'])}")
            logger.info(f"    Updated: {format_datetime(row['updated_at'])}")
            logger.info(f"    Accessed: {format_datetime(row['last_accessed_at'])}")

        logger.info("")


async def show_stats(conn: asyncpg.Connection):
    """Show memory system statistics."""
    # Total memories
    total = await conn.fetchval("SELECT COUNT(*) FROM memories")

    # By privacy level
    privacy_stats = await conn.fetch(
        "SELECT privacy_level, COUNT(*) as count FROM memories GROUP BY privacy_level ORDER BY count DESC"
    )

    # By memory type
    type_stats = await conn.fetch(
        "SELECT memory_type, COUNT(*) as count FROM memories GROUP BY memory_type ORDER BY count DESC"
    )

    # By user
    user_stats = await conn.fetch(
        "SELECT user_id, COUNT(*) as count FROM memories GROUP BY user_id ORDER BY count DESC LIMIT 10"
    )

    # Recent activity
    recent = await conn.fetchval(
        "SELECT COUNT(*) FROM memories WHERE created_at > NOW() - INTERVAL '7 days'"
    )

    logger.info("\n" + "=" * 60)
    logger.info("MEMORY SYSTEM STATISTICS")
    logger.info("=" * 60)

    logger.info(f"\nTotal memories: {total}")
    logger.info(f"Created in last 7 days: {recent}")

    logger.info("\nBy Privacy Level:")
    for row in privacy_stats:
        pct = (row["count"] / total * 100) if total > 0 else 0
        logger.info(f"  {row['privacy_level']:20} {row['count']:6} ({pct:5.1f}%)")

    logger.info("\nBy Memory Type:")
    for row in type_stats:
        pct = (row["count"] / total * 100) if total > 0 else 0
        logger.info(f"  {row['memory_type']:20} {row['count']:6} ({pct:5.1f}%)")

    logger.info("\nTop 10 Users by Memory Count:")
    for row in user_stats:
        logger.info(f"  User {row['user_id']:20} {row['count']:6} memories")


async def inspect_memory(conn: asyncpg.Connection, memory_id: int):
    """Show full details for a specific memory."""
    row = await conn.fetchrow(
        """
        SELECT id, user_id, topic_summary, raw_dialogue, memory_type,
               privacy_level, confidence, origin_guild_id, origin_channel_id,
               created_at, updated_at, last_accessed_at
        FROM memories
        WHERE id = $1
        """,
        memory_id,
    )

    if not row:
        logger.error(f"Memory {memory_id} not found")
        return

    logger.info("\n" + "=" * 60)
    logger.info(f"MEMORY #{row['id']}")
    logger.info("=" * 60)

    logger.info(f"\nUser ID: {row['user_id']}")
    logger.info(f"Type: {row['memory_type']}")
    logger.info(f"Privacy: {row['privacy_level']}")
    logger.info(f"Confidence: {row['confidence']:.2f}")
    logger.info(f"\nOrigin Guild: {row['origin_guild_id']}")
    logger.info(f"Origin Channel: {row['origin_channel_id']}")
    logger.info(f"\nCreated: {format_datetime(row['created_at'])}")
    logger.info(f"Updated: {format_datetime(row['updated_at'])}")
    logger.info(f"Last Accessed: {format_datetime(row['last_accessed_at'])}")

    logger.info(f"\n{'='*40}")
    logger.info("SUMMARY")
    logger.info("=" * 40)
    logger.info(row["topic_summary"])

    logger.info(f"\n{'='*40}")
    logger.info("RAW DIALOGUE")
    logger.info("=" * 40)
    logger.info(row["raw_dialogue"] or "(empty)")


async def search_memories(
    conn: asyncpg.Connection, query: str, limit: int = 20
):
    """Search memories by text content."""
    rows = await conn.fetch(
        """
        SELECT id, user_id, topic_summary, memory_type, privacy_level, updated_at
        FROM memories
        WHERE topic_summary ILIKE $1 OR raw_dialogue ILIKE $1
        ORDER BY updated_at DESC
        LIMIT $2
        """,
        f"%{query}%",
        limit,
    )

    if not rows:
        logger.info(f"No memories found matching '{query}'")
        return

    logger.info(f"\n{'='*80}")
    logger.info(f"Found {len(rows)} memories matching '{query}'")
    logger.info(f"{'='*80}\n")

    for row in rows:
        logger.info(f"[{row['id']}] User {row['user_id']} | {row['memory_type']} | {row['privacy_level']}")
        logger.info(f"    {truncate(row['topic_summary'], 70)}")
        logger.info(f"    Updated: {format_datetime(row['updated_at'])}")
        logger.info("")


async def export_memories(
    conn: asyncpg.Connection,
    output_file: str,
    user_id: int = None,
    guild_id: int = None,
):
    """Export memories to JSON file."""
    conditions = []
    params = []
    param_idx = 1

    if user_id:
        conditions.append(f"user_id = ${param_idx}")
        params.append(user_id)
        param_idx += 1

    if guild_id:
        conditions.append(f"origin_guild_id = ${param_idx}")
        params.append(guild_id)
        param_idx += 1

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    rows = await conn.fetch(
        f"""
        SELECT id, user_id, topic_summary, raw_dialogue, memory_type,
               privacy_level, confidence, origin_guild_id, origin_channel_id,
               created_at, updated_at, last_accessed_at
        FROM memories
        WHERE {where_clause}
        ORDER BY id
        """,
        *params,
    )

    memories = []
    for row in rows:
        memories.append({
            "id": row["id"],
            "user_id": row["user_id"],
            "topic_summary": row["topic_summary"],
            "raw_dialogue": row["raw_dialogue"],
            "memory_type": row["memory_type"],
            "privacy_level": row["privacy_level"],
            "confidence": float(row["confidence"]),
            "origin_guild_id": row["origin_guild_id"],
            "origin_channel_id": row["origin_channel_id"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "last_accessed_at": row["last_accessed_at"].isoformat() if row["last_accessed_at"] else None,
        })

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(memories, f, indent=2, ensure_ascii=False)

    logger.info(f"Exported {len(memories)} memories to {output_file}")


async def main_async(args):
    """Async main function."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL environment variable required")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)

    try:
        if args.command == "list":
            await list_memories(
                conn,
                user_id=args.user_id,
                privacy_level=args.privacy,
                guild_id=args.guild_id,
                verbose=args.verbose,
                limit=args.limit,
            )
        elif args.command == "stats":
            await show_stats(conn)
        elif args.command == "inspect":
            await inspect_memory(conn, args.memory_id)
        elif args.command == "search":
            await search_memories(conn, args.query, limit=args.limit)
        elif args.command == "export":
            # Validate --all is not used with filters
            if getattr(args, 'all', False) and (args.user_id or args.guild_id):
                logger.error("Cannot use --all with --user-id or --guild-id filters")
                sys.exit(1)
            # If no --all and no filters, warn user
            if not getattr(args, 'all', False) and not args.user_id and not args.guild_id:
                logger.warning("No filters specified. Use --all to explicitly export all memories.")
            await export_memories(
                conn,
                output_file=args.output,
                user_id=args.user_id,
                guild_id=args.guild_id,
            )
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Memory Inspector CLI - Debug and query the memory system"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_parser = subparsers.add_parser("list", help="List memories")
    list_parser.add_argument("--user-id", type=int, help="Filter by user ID")
    list_parser.add_argument("--guild-id", type=int, help="Filter by guild ID")
    list_parser.add_argument(
        "--privacy",
        choices=["dm", "channel_restricted", "guild_public", "global"],
        help="Filter by privacy level",
    )
    list_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show full details"
    )
    list_parser.add_argument(
        "--limit", type=int, default=50, help="Max results (default: 50)"
    )

    # Stats command
    subparsers.add_parser("stats", help="Show memory statistics")

    # Inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a specific memory")
    inspect_parser.add_argument("--memory-id", type=int, required=True, help="Memory ID")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search memories by content")
    search_parser.add_argument("--query", "-q", required=True, help="Search query")
    search_parser.add_argument(
        "--limit", type=int, default=20, help="Max results (default: 20)"
    )

    # Export command
    export_parser = subparsers.add_parser("export", help="Export memories to JSON")
    export_parser.add_argument(
        "--output", "-o", required=True, help="Output file path"
    )
    export_parser.add_argument(
        "--all", "-a", action="store_true",
        help="Export ALL memories (for backup). Mutually exclusive with filters."
    )
    export_parser.add_argument("--user-id", type=int, help="Filter by user ID")
    export_parser.add_argument("--guild-id", type=int, help="Filter by guild ID")

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
