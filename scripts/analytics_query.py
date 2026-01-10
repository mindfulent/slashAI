#!/usr/bin/env python3
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
CLI tool for querying analytics data.

Usage:
    python scripts/analytics_query.py dau              # Daily active users
    python scripts/analytics_query.py tokens           # Token usage
    python scripts/analytics_query.py commands         # Command usage
    python scripts/analytics_query.py errors           # Recent errors
    python scripts/analytics_query.py summary          # Overall summary
    python scripts/analytics_query.py latency          # Response latency percentiles
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

QUERIES = {
    "dau": """
        SELECT DATE(created_at) as day, COUNT(DISTINCT user_id) as users, COUNT(*) as messages
        FROM analytics_events
        WHERE event_name = 'message_received' AND created_at > NOW() - INTERVAL '14 days'
        GROUP BY DATE(created_at) ORDER BY day DESC
    """,
    "tokens": """
        SELECT DATE(created_at) as day,
               SUM((properties->>'input_tokens')::int) as input,
               SUM((properties->>'output_tokens')::int) as output,
               COALESCE(SUM((properties->>'cache_read')::int), 0) as cache_hits
        FROM analytics_events
        WHERE event_name = 'claude_api_call' AND created_at > NOW() - INTERVAL '14 days'
        GROUP BY DATE(created_at) ORDER BY day DESC
    """,
    "commands": """
        SELECT properties->>'command_name' || ' ' || COALESCE(properties->>'subcommand', '') as cmd,
               COUNT(*) as count,
               COUNT(DISTINCT user_id) as users
        FROM analytics_events
        WHERE event_name = 'command_used' AND created_at > NOW() - INTERVAL '30 days'
        GROUP BY properties->>'command_name', properties->>'subcommand'
        ORDER BY count DESC
    """,
    "errors": """
        SELECT created_at, properties->>'error_type' as type,
               LEFT(properties->>'error_message', 80) as message
        FROM analytics_events
        WHERE event_category = 'error' AND created_at > NOW() - INTERVAL '7 days'
        ORDER BY created_at DESC LIMIT 20
    """,
    "summary": """
        SELECT
            COUNT(*) FILTER (WHERE event_name = 'message_received') as messages,
            COUNT(DISTINCT user_id) FILTER (WHERE event_name = 'message_received') as users,
            COUNT(*) FILTER (WHERE event_name = 'memory_created') as memories_created,
            COUNT(*) FILTER (WHERE event_category = 'error') as errors,
            COALESCE(SUM((properties->>'input_tokens')::int) FILTER (WHERE event_name = 'claude_api_call'), 0) as input_tokens,
            COALESCE(SUM((properties->>'output_tokens')::int) FILTER (WHERE event_name = 'claude_api_call'), 0) as output_tokens
        FROM analytics_events
        WHERE created_at > NOW() - INTERVAL '24 hours'
    """,
    "latency": """
        SELECT
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY (properties->>'latency_ms')::int) as p50_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY (properties->>'latency_ms')::int) as p95_ms,
            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY (properties->>'latency_ms')::int) as p99_ms,
            COUNT(*) as total_responses
        FROM analytics_events
        WHERE event_name = 'response_sent' AND created_at > NOW() - INTERVAL '24 hours'
    """,
    "memory": """
        SELECT
            COUNT(*) FILTER (WHERE event_name = 'extraction_triggered') as extractions,
            COUNT(*) FILTER (WHERE event_name = 'memory_created') as created,
            COUNT(*) FILTER (WHERE event_name = 'retrieval_performed') as retrievals,
            COUNT(*) FILTER (WHERE event_name = 'extraction_failed') as failures,
            ROUND(AVG((properties->>'results_count')::float) FILTER (WHERE event_name = 'retrieval_performed')::numeric, 2) as avg_results
        FROM analytics_events
        WHERE event_category = 'memory' AND created_at > NOW() - INTERVAL '7 days'
    """,
    "tools": """
        SELECT
            properties->>'tool_name' as tool,
            COUNT(*) as executions,
            COUNT(*) FILTER (WHERE (properties->>'success')::boolean) as successes,
            ROUND(AVG((properties->>'latency_ms')::int)::numeric, 0) as avg_latency_ms
        FROM analytics_events
        WHERE event_name = 'tool_executed' AND created_at > NOW() - INTERVAL '30 days'
        GROUP BY properties->>'tool_name'
        ORDER BY executions DESC
    """,
}


async def run_query(query_name: str):
    """Run a predefined query and print results."""
    if query_name not in QUERIES:
        print(f"Unknown query: {query_name}")
        print(f"Available: {', '.join(QUERIES.keys())}")
        return

    if not DATABASE_URL:
        print("Error: DATABASE_URL not set")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(QUERIES[query_name])
        if not rows:
            print("No data found")
            return

        # Print header
        columns = list(rows[0].keys())
        widths = []
        for col in columns:
            max_len = max(len(str(col)), max(len(str(row[col])) for row in rows))
            widths.append(min(max_len, 20))  # Cap at 20 chars

        header = " | ".join(f"{col:>{widths[i]}}" for i, col in enumerate(columns))
        print(header)
        print("-" * len(header))

        # Print rows
        for row in rows:
            values = []
            for i, col in enumerate(columns):
                val = row[col]
                if val is None:
                    val = "-"
                val_str = str(val)
                if len(val_str) > widths[i]:
                    val_str = val_str[: widths[i] - 2] + ".."
                values.append(f"{val_str:>{widths[i]}}")
            print(" | ".join(values))

    finally:
        await conn.close()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python analytics_query.py <query_name>")
        print(f"Available queries: {', '.join(QUERIES.keys())}")
        print("\nExamples:")
        print("  python analytics_query.py summary    # 24-hour overview")
        print("  python analytics_query.py dau        # Daily active users")
        print("  python analytics_query.py tokens     # Token usage by day")
        print("  python analytics_query.py commands   # Command usage")
        print("  python analytics_query.py errors     # Recent errors")
        print("  python analytics_query.py latency    # Response latency")
        print("  python analytics_query.py memory     # Memory system stats")
        print("  python analytics_query.py tools      # Tool execution stats")
        sys.exit(1)

    asyncio.run(run_query(sys.argv[1]))


if __name__ == "__main__":
    main()
