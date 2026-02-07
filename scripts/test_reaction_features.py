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
Acceptance Test Script for Reaction Memory Features (v0.12.6 / v0.12.7)

Usage:
    python scripts/test_reaction_features.py promotion [--apply]
    python scripts/test_reaction_features.py extraction
    python scripts/test_reaction_features.py status
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

import asyncpg
from dotenv import load_dotenv

load_dotenv()


async def get_db():
    """Get database connection."""
    return await asyncpg.connect(os.environ["DATABASE_URL"])


async def test_promotion(apply: bool = False):
    """
    Test v0.12.6 Memory Promotion.

    Checks for community_observation memories that meet promotion criteria.
    With --apply, temporarily bypasses age requirement to test promotion flow.
    """
    db = await get_db()

    print("=" * 60)
    print("v0.12.6 Memory Promotion Test")
    print("=" * 60)
    print()

    # Find promotion candidates
    candidates = await db.fetch("""
        SELECT
            m.id,
            m.topic_summary,
            m.memory_type,
            m.reaction_summary,
            m.created_at,
            m.user_id
        FROM memories m
        WHERE m.memory_type = 'community_observation'
        AND m.reaction_summary IS NOT NULL
    """)

    eligible = []

    for c in candidates:
        rs = c["reaction_summary"]
        if isinstance(rs, str):
            rs = json.loads(rs)

        total = rs.get("total_reactions", 0)
        unique = rs.get("unique_reactors", 0)
        sentiment = rs.get("sentiment_score", 0)
        controversy = rs.get("controversy_score", 0)
        age_days = (datetime.now(c["created_at"].tzinfo) - c["created_at"]).days

        # Check criteria (age optional in test mode)
        meets_reactions = total >= 4
        meets_unique = unique >= 3
        meets_sentiment = sentiment >= 0.6
        meets_controversy = controversy <= 0.3
        meets_age = age_days >= 3

        # For test, eligible if all non-age criteria met
        test_eligible = all([meets_reactions, meets_unique, meets_sentiment, meets_controversy])
        prod_eligible = test_eligible and meets_age

        if test_eligible:
            topic = c["topic_summary"][:50] if c["topic_summary"] else "N/A"
            eligible.append({
                "id": c["id"],
                "topic": topic,
                "reactions": total,
                "unique_reactors": unique,
                "sentiment": sentiment,
                "controversy": controversy,
                "age_days": age_days,
                "prod_eligible": prod_eligible,
            })

    print(f"Found {len(eligible)} memories eligible for promotion (ignoring age):")
    print()

    for e in eligible:
        status = "READY" if e["prod_eligible"] else f"WAITING ({3 - e['age_days']} days)"
        print(f"  Memory {e['id']}: {status}")
        print(f"    Topic: {e['topic']}...")
        print(f"    Reactions: {e['reactions']}, Unique: {e['unique_reactors']}")
        print(f"    Sentiment: {e['sentiment']:.2f}, Controversy: {e['controversy']:.2f}")
        print(f"    Age: {e['age_days']} days")
        print()

    if apply and eligible:
        print("-" * 60)
        print("APPLYING TEST PROMOTION (bypassing age requirement)...")
        print()

        # Promote first eligible memory
        test_memory = eligible[0]
        await db.execute("""
            UPDATE memories
            SET memory_type = 'semantic',
                confidence = GREATEST(confidence, 0.8)
            WHERE id = $1
        """, test_memory["id"])

        print(f"Promoted Memory {test_memory['id']} to 'semantic' type")
        print("Check with: python scripts/memory_inspector.py inspect --memory-id", test_memory["id"])

        # Note: In real usage, we'd also track analytics
    elif not eligible:
        print("No eligible candidates found.")
        print("Ensure community_observation memories have 4+ reactions from 3+ unique users.")

    await db.close()


async def test_extraction():
    """
    Test v0.12.7 Extraction Enhancement.

    Shows how to verify reaction context is included in extraction prompts.
    """
    db = await get_db()

    print("=" * 60)
    print("v0.12.7 Extraction Enhancement Test")
    print("=" * 60)
    print()

    # Check for sessions with message_ids
    sessions = await db.fetch("""
        SELECT id, user_id, channel_id, message_count, messages
        FROM memory_sessions
        WHERE message_count >= 5
        ORDER BY id DESC
        LIMIT 5
    """)

    print(f"Found {len(sessions)} sessions with 5+ messages (extraction eligible):")
    print()

    for s in sessions:
        messages = s["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)

        msg_ids = [m.get("message_id") for m in messages if m.get("message_id")]

        print(f"  Session {s['id']}:")
        print(f"    User: {s['user_id']}")
        print(f"    Channel: {s['channel_id']}")
        print(f"    Messages: {s['message_count']}")
        print(f"    Has message_ids: {len(msg_ids) > 0} ({len(msg_ids)} tracked)")

        # Check if any of these messages have reactions
        if msg_ids:
            reactions = await db.fetch("""
                SELECT message_id, COUNT(*) as count
                FROM message_reactions
                WHERE message_id = ANY($1::bigint[]) AND removed_at IS NULL
                GROUP BY message_id
            """, msg_ids)

            if reactions:
                print(f"    Messages with reactions: {len(reactions)}")
                for r in reactions[:3]:
                    print(f"      msg {r['message_id']}: {r['count']} reaction(s)")
        print()

    print("-" * 60)
    print("To test v0.12.7:")
    print("1. Restart the bot to load new code")
    print("2. Have a conversation (5+ exchanges)")
    print("3. React to messages during the conversation")
    print("4. When extraction triggers, reaction context will be included")
    print("5. Check logs for 'Reaction Context' in extraction prompt")
    print()

    await db.close()


async def show_status():
    """Show overall status of reaction features."""
    db = await get_db()

    print("=" * 60)
    print("Reaction Memory Features Status")
    print("=" * 60)
    print()

    # Count reaction data
    stats = {}

    stats["total_reactions"] = (await db.fetchval(
        "SELECT COUNT(*) FROM message_reactions WHERE removed_at IS NULL"
    ))

    stats["unique_messages"] = (await db.fetchval(
        "SELECT COUNT(DISTINCT message_id) FROM message_reactions WHERE removed_at IS NULL"
    ))

    stats["community_observations"] = (await db.fetchval(
        "SELECT COUNT(*) FROM memories WHERE memory_type = 'community_observation'"
    ))

    stats["inferred_preferences"] = (await db.fetchval(
        "SELECT COUNT(*) FROM memories WHERE memory_type = 'inferred_preference'"
    ))

    stats["with_reaction_summary"] = (await db.fetchval(
        "SELECT COUNT(*) FROM memories WHERE reaction_summary IS NOT NULL"
    ))

    stats["promoted_from_community"] = (await db.fetchval("""
        SELECT COUNT(*) FROM memories
        WHERE memory_type = 'semantic'
        AND reaction_confidence_boost > 0
    """))

    stats["memory_message_links"] = (await db.fetchval(
        "SELECT COUNT(*) FROM memory_message_links"
    ))

    print("Database Statistics:")
    print(f"  Total reactions tracked: {stats['total_reactions']}")
    print(f"  Unique messages with reactions: {stats['unique_messages']}")
    print(f"  Memory-message links: {stats['memory_message_links']}")
    print()
    print("Memory Types:")
    print(f"  Community observations: {stats['community_observations']}")
    print(f"  Inferred preferences: {stats['inferred_preferences']}")
    print(f"  With reaction_summary: {stats['with_reaction_summary']}")
    print(f"  Promoted from community: {stats['promoted_from_community']}")
    print()

    # Check for eligible promotions
    eligible = await db.fetchval("""
        SELECT COUNT(*)
        FROM memories m
        WHERE m.memory_type = 'community_observation'
        AND m.reaction_summary IS NOT NULL
        AND (m.reaction_summary->>'total_reactions')::int >= 4
        AND (m.reaction_summary->>'unique_reactors')::int >= 3
        AND (m.reaction_summary->>'sentiment_score')::float >= 0.6
        AND (m.reaction_summary->>'controversy_score')::float <= 0.3
    """)

    eligible_now = await db.fetchval("""
        SELECT COUNT(*)
        FROM memories m
        WHERE m.memory_type = 'community_observation'
        AND m.reaction_summary IS NOT NULL
        AND (m.reaction_summary->>'total_reactions')::int >= 4
        AND (m.reaction_summary->>'unique_reactors')::int >= 3
        AND (m.reaction_summary->>'sentiment_score')::float >= 0.6
        AND (m.reaction_summary->>'controversy_score')::float <= 0.3
        AND m.created_at < NOW() - INTERVAL '3 days'
    """)

    print("Promotion Status:")
    print(f"  Eligible (ignoring age): {eligible}")
    print(f"  Eligible now (age >= 3 days): {eligible_now}")
    print()

    await db.close()


def main():
    parser = argparse.ArgumentParser(description="Test reaction memory features")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # promotion subcommand
    promo_parser = subparsers.add_parser("promotion", help="Test v0.12.6 Memory Promotion")
    promo_parser.add_argument("--apply", action="store_true", help="Apply test promotion")

    # extraction subcommand
    subparsers.add_parser("extraction", help="Test v0.12.7 Extraction Enhancement")

    # status subcommand
    subparsers.add_parser("status", help="Show reaction features status")

    args = parser.parse_args()

    if args.command == "promotion":
        asyncio.run(test_promotion(apply=args.apply))
    elif args.command == "extraction":
        asyncio.run(test_extraction())
    elif args.command == "status":
        asyncio.run(show_status())


if __name__ == "__main__":
    main()
