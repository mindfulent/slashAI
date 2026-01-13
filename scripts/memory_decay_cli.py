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
Memory Decay CLI

Command-line tool for managing memory confidence decay.

Usage:
    # Preview decay without applying changes
    python scripts/memory_decay_cli.py run --dry-run

    # Run decay job manually
    python scripts/memory_decay_cli.py run

    # Protect a memory from decay
    python scripts/memory_decay_cli.py protect 42

    # Unprotect a memory
    python scripts/memory_decay_cli.py unprotect 42

    # Show consolidation candidates (episodic memories worth promoting)
    python scripts/memory_decay_cli.py candidates

    # Show decay statistics
    python scripts/memory_decay_cli.py stats

    # Show memories pending deletion
    python scripts/memory_decay_cli.py pending
"""

import argparse
import asyncio
import logging
import os
import sys

import asyncpg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)


def format_confidence(conf: float) -> str:
    """Format confidence with color indicator."""
    if conf >= 0.9:
        return f"{conf:.2f} (high)"
    elif conf >= 0.7:
        return f"{conf:.2f} (good)"
    elif conf >= 0.5:
        return f"{conf:.2f} (moderate)"
    elif conf >= 0.3:
        return f"{conf:.2f} (low)"
    else:
        return f"{conf:.2f} (very low)"


def truncate(text: str, max_len: int = 50) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


async def run_decay(pool: asyncpg.Pool, dry_run: bool = False) -> None:
    """Run the decay job or preview what would change."""
    # Add src to path for imports
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from memory.decay import run_decay_job
    from memory.config import MemoryConfig

    config = MemoryConfig.from_env()

    if dry_run:
        # Preview what would be decayed (with explicit type casts for PostgreSQL)
        would_decay = await pool.fetch(
            f"""
            SELECT
                id, user_id, topic_summary, confidence, retrieval_count, last_accessed_at,
                confidence * POWER(
                    $1::float + (($2::float - $1::float) * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10.0)),
                    FLOOR(EXTRACT(EPOCH FROM (NOW() - last_accessed_at)) / 86400.0 / $3::float)
                ) as new_confidence,
                $1::float + (($2::float - $1::float) * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10.0)) as decay_rate
            FROM memories
            WHERE decay_policy = 'standard'
              AND is_protected = FALSE
              AND last_accessed_at < NOW() - INTERVAL '{config.decay_period_days} days'
              AND confidence > $4::float
            ORDER BY (confidence - confidence * POWER(
                $1::float + (($2::float - $1::float) * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10.0)),
                FLOOR(EXTRACT(EPOCH FROM (NOW() - last_accessed_at)) / 86400.0 / $3::float)
            )) DESC
            LIMIT 20
        """,
            config.base_decay_rate,
            config.max_decay_rate,
            float(config.decay_period_days),
            config.min_confidence,
        )

        if not would_decay:
            print("No memories eligible for decay.")
            return

        print(f"Would decay {len(would_decay)} memories (showing top 20):")
        print("-" * 90)
        print(f"{'ID':<6} {'Conf':<6} {'New':<6} {'Rate':<6} {'Retr':<5} {'Summary':<50}")
        print("-" * 90)

        for m in would_decay:
            print(
                f"{m['id']:<6} "
                f"{m['confidence']:.2f}  "
                f"{m['new_confidence']:.2f}  "
                f"{m['decay_rate']:.2f}  "
                f"{m['retrieval_count'] or 0:<5} "
                f"{truncate(m['topic_summary'])}"
            )
    else:
        print("Running decay job...")
        stats = await run_decay_job(pool, config)
        print(f"Decay complete:")
        print(f"  Memories decayed: {stats.decayed_count}")
        print(f"  Flagged for cleanup: {stats.cleanup_flagged}")
        print(f"  Consolidation candidates: {stats.consolidation_candidates}")


async def protect_memory(pool: asyncpg.Pool, memory_id: int) -> None:
    """Protect a memory from decay."""
    result = await pool.fetchrow(
        """
        UPDATE memories
        SET is_protected = TRUE, decay_policy = 'none'
        WHERE id = $1
        RETURNING id, topic_summary, user_id
    """,
        memory_id,
    )

    if result:
        print(f"Protected memory {memory_id}:")
        print(f"  User: {result['user_id']}")
        print(f"  Summary: {truncate(result['topic_summary'], 70)}")
    else:
        print(f"Memory {memory_id} not found")


async def unprotect_memory(pool: asyncpg.Pool, memory_id: int) -> None:
    """Remove protection from a memory."""
    result = await pool.fetchrow(
        """
        UPDATE memories
        SET is_protected = FALSE, decay_policy = 'standard'
        WHERE id = $1
        RETURNING id, topic_summary, user_id, memory_type
    """,
        memory_id,
    )

    if result:
        # Semantic memories should keep 'none' decay policy
        if result["memory_type"] == "semantic":
            await pool.execute(
                "UPDATE memories SET decay_policy = 'none' WHERE id = $1", memory_id
            )
            print(f"Unprotected memory {memory_id} (but kept 'none' decay policy for semantic type)")
        else:
            print(f"Unprotected memory {memory_id}:")
        print(f"  User: {result['user_id']}")
        print(f"  Type: {result['memory_type']}")
        print(f"  Summary: {truncate(result['topic_summary'], 70)}")
    else:
        print(f"Memory {memory_id} not found")


async def show_candidates(pool: asyncpg.Pool) -> None:
    """Show consolidation candidates (frequently-accessed episodic memories)."""
    candidates = await pool.fetch(
        """
        SELECT id, user_id, topic_summary, memory_type, retrieval_count, confidence
        FROM memories
        WHERE memory_type = 'episodic'
          AND retrieval_count >= 5
          AND confidence > 0.6
          AND decay_policy != 'none'
        ORDER BY retrieval_count DESC
        LIMIT 20
    """
    )

    if not candidates:
        print("No consolidation candidates found.")
        print("(Episodic memories with 5+ retrievals and >0.6 confidence)")
        return

    print(f"Found {len(candidates)} consolidation candidates:")
    print("These episodic memories have been frequently retrieved and may be worth")
    print("promoting to semantic type (facts) using: UPDATE memories SET memory_type = 'semantic'")
    print("-" * 90)
    print(f"{'ID':<6} {'User':<20} {'Retr':<6} {'Conf':<6} {'Summary':<45}")
    print("-" * 90)

    for c in candidates:
        print(
            f"{c['id']:<6} "
            f"{c['user_id']:<20} "
            f"{c['retrieval_count']:<6} "
            f"{c['confidence']:.2f}  "
            f"{truncate(c['topic_summary'], 45)}"
        )


async def show_stats(pool: asyncpg.Pool) -> None:
    """Show decay-related statistics."""
    # Overall stats
    overall = await pool.fetchrow(
        """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE is_protected = TRUE) as protected,
            COUNT(*) FILTER (WHERE decay_policy = 'none') as no_decay,
            COUNT(*) FILTER (WHERE decay_policy = 'standard') as standard_decay,
            COUNT(*) FILTER (WHERE decay_policy = 'pending_deletion') as pending_deletion,
            AVG(confidence) as avg_confidence,
            AVG(retrieval_count) as avg_retrievals
        FROM memories
    """
    )

    # Confidence distribution
    distribution = await pool.fetch(
        """
        SELECT
            CASE
                WHEN confidence >= 0.9 THEN 'high (0.9+)'
                WHEN confidence >= 0.7 THEN 'good (0.7-0.9)'
                WHEN confidence >= 0.5 THEN 'moderate (0.5-0.7)'
                WHEN confidence >= 0.3 THEN 'low (0.3-0.5)'
                ELSE 'very low (<0.3)'
            END as tier,
            COUNT(*) as count,
            ROUND(AVG(confidence)::numeric, 2) as avg_conf
        FROM memories
        GROUP BY 1
        ORDER BY MIN(confidence) DESC
    """
    )

    # Stale memories
    stale = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM memories
        WHERE last_accessed_at < NOW() - INTERVAL '60 days'
          AND decay_policy = 'standard'
    """
    )

    # Active memories this week
    active = await pool.fetchrow(
        """
        SELECT COUNT(*) as count, COALESCE(AVG(retrieval_count), 0) as avg_retrievals
        FROM memories
        WHERE last_accessed_at > NOW() - INTERVAL '7 days'
    """
    )

    print("Memory Decay Statistics")
    print("=" * 50)
    print()
    print("Overall:")
    print(f"  Total memories: {overall['total']}")
    print(f"  Protected: {overall['protected']}")
    print(f"  No decay (semantic): {overall['no_decay']}")
    print(f"  Standard decay: {overall['standard_decay']}")
    print(f"  Pending deletion: {overall['pending_deletion']}")
    print(f"  Average confidence: {overall['avg_confidence']:.2f}" if overall["avg_confidence"] else "  Average confidence: N/A")
    print(f"  Average retrievals: {overall['avg_retrievals']:.1f}" if overall["avg_retrievals"] else "  Average retrievals: N/A")
    print()
    print("Confidence Distribution:")
    for row in distribution:
        print(f"  {row['tier']:<20} {row['count']:>5} memories (avg: {row['avg_conf']})")
    print()
    print("Activity:")
    print(f"  Stale (60+ days): {stale}")
    print(f"  Active this week: {active['count']} (avg {active['avg_retrievals']:.1f} retrievals)")


async def show_pending(pool: asyncpg.Pool) -> None:
    """Show memories pending deletion."""
    pending = await pool.fetch(
        """
        SELECT id, user_id, topic_summary, confidence, created_at, last_accessed_at
        FROM memories
        WHERE decay_policy = 'pending_deletion'
        ORDER BY confidence ASC, created_at ASC
        LIMIT 30
    """
    )

    if not pending:
        print("No memories pending deletion.")
        return

    print(f"Found {len(pending)} memories pending deletion:")
    print("These memories have very low confidence and are old.")
    print("To delete: DELETE FROM memories WHERE decay_policy = 'pending_deletion'")
    print("-" * 90)
    print(f"{'ID':<6} {'User':<20} {'Conf':<6} {'Summary':<50}")
    print("-" * 90)

    for m in pending:
        print(
            f"{m['id']:<6} "
            f"{m['user_id']:<20} "
            f"{m['confidence']:.2f}  "
            f"{truncate(m['topic_summary'], 50)}"
        )


async def main():
    parser = argparse.ArgumentParser(description="Memory decay management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run decay job")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without applying"
    )

    # protect command
    protect_parser = subparsers.add_parser("protect", help="Protect a memory from decay")
    protect_parser.add_argument("memory_id", type=int, help="Memory ID to protect")

    # unprotect command
    unprotect_parser = subparsers.add_parser(
        "unprotect", help="Remove protection from a memory"
    )
    unprotect_parser.add_argument("memory_id", type=int, help="Memory ID to unprotect")

    # candidates command
    subparsers.add_parser(
        "candidates", help="Show consolidation candidates"
    )

    # stats command
    subparsers.add_parser("stats", help="Show decay statistics")

    # pending command
    subparsers.add_parser("pending", help="Show memories pending deletion")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("Error: DATABASE_URL environment variable not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        if args.command == "run":
            await run_decay(pool, args.dry_run)
        elif args.command == "protect":
            await protect_memory(pool, args.memory_id)
        elif args.command == "unprotect":
            await unprotect_memory(pool, args.memory_id)
        elif args.command == "candidates":
            await show_candidates(pool)
        elif args.command == "stats":
            await show_stats(pool)
        elif args.command == "pending":
            await show_pending(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
