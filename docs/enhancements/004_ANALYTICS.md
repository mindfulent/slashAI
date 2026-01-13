# slashAI Native Analytics Plan

## Overview

Add lightweight event tracking to slashAI using the existing PostgreSQL database. This provides visibility into bot usage patterns, performance metrics, and user engagement without introducing external dependencies.

**Goals:**
- Track user interactions (messages, commands, tool usage)
- Monitor system performance (latency, token usage, error rates)
- Understand memory system effectiveness (extractions, retrievals)
- Enable cost estimation (API token consumption)

**Non-goals:**
- Real-time dashboards (query on-demand)
- User behavior profiling (privacy-first approach)
- Complex funnel analysis (keep it simple)

---

## Phase 1: Database Schema

### Migration 009: Create Analytics Tables

```sql
-- Migration 009: Create analytics events table
-- Lightweight event tracking for usage metrics and performance monitoring

CREATE TABLE analytics_events (
    id BIGSERIAL PRIMARY KEY,

    -- Event identification
    event_name TEXT NOT NULL,
    event_category TEXT NOT NULL,

    -- Context (nullable for system events)
    user_id BIGINT,
    channel_id BIGINT,
    guild_id BIGINT,

    -- Flexible event data
    properties JSONB DEFAULT '{}'::jsonb,

    -- Timing
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT event_category_valid
        CHECK (event_category IN ('message', 'memory', 'command', 'tool', 'api', 'error', 'system'))
);

-- Indexes for common query patterns
CREATE INDEX idx_events_created_at ON analytics_events (created_at DESC);
CREATE INDEX idx_events_name ON analytics_events (event_name);
CREATE INDEX idx_events_category ON analytics_events (event_category);
CREATE INDEX idx_events_user_id ON analytics_events (user_id) WHERE user_id IS NOT NULL;

-- Composite index for time-range queries by category
CREATE INDEX idx_events_category_time ON analytics_events (event_category, created_at DESC);

-- GIN index for JSONB property queries
CREATE INDEX idx_events_properties ON analytics_events USING GIN (properties);

-- Optional: Partitioning by month for long-term performance
-- (Uncomment if data volume becomes significant)
-- CREATE TABLE analytics_events (
--     ...
-- ) PARTITION BY RANGE (created_at);
```

### Event Categories

| Category | Description | Example Events |
|----------|-------------|----------------|
| `message` | User interactions | `message_received`, `response_sent` |
| `memory` | Memory system ops | `extraction_triggered`, `memory_created`, `retrieval_performed` |
| `command` | Slash commands | `command_used` |
| `tool` | Agentic tool usage | `tool_executed` |
| `api` | External API calls | `claude_api_call`, `voyage_api_call` |
| `error` | Failures | `api_error`, `extraction_failed` |
| `system` | Bot lifecycle | `bot_started`, `bot_shutdown` |

---

## Phase 2: Python Analytics Module

### File: `src/analytics.py`

```python
"""
Lightweight analytics tracking for slashAI.

Usage:
    from analytics import track, track_async

    # Synchronous (fire-and-forget, uses background task)
    track("message_received", "message", user_id=123, properties={"channel_type": "dm"})

    # Async (when you need to await completion)
    await track_async("command_used", "command", user_id=123, properties={"command": "memories list"})
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)

# Module-level connection pool (initialized lazily)
_pool: Optional[asyncpg.Pool] = None
_enabled: bool = os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"


async def _get_pool() -> Optional[asyncpg.Pool]:
    """Get or create the connection pool."""
    global _pool
    if _pool is None and _enabled:
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            try:
                _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
            except Exception as e:
                logger.warning(f"Analytics pool creation failed: {e}")
                return None
    return _pool


async def track_async(
    event_name: str,
    event_category: str,
    user_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    guild_id: Optional[int] = None,
    properties: Optional[dict[str, Any]] = None,
) -> bool:
    """
    Track an event asynchronously.

    Args:
        event_name: Specific event identifier (e.g., "message_received")
        event_category: One of: message, memory, command, tool, api, error, system
        user_id: Discord user ID (optional)
        channel_id: Discord channel ID (optional)
        guild_id: Discord guild ID (optional)
        properties: Additional event data as key-value pairs

    Returns:
        True if event was recorded, False otherwise
    """
    if not _enabled:
        return False

    pool = await _get_pool()
    if pool is None:
        return False

    try:
        import json
        await pool.execute(
            """
            INSERT INTO analytics_events
                (event_name, event_category, user_id, channel_id, guild_id, properties)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            event_name,
            event_category,
            user_id,
            channel_id,
            guild_id,
            json.dumps(properties or {}),
        )
        return True
    except Exception as e:
        logger.debug(f"Analytics tracking failed: {e}")
        return False


def track(
    event_name: str,
    event_category: str,
    user_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    guild_id: Optional[int] = None,
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """
    Track an event (fire-and-forget).

    Creates a background task to record the event without blocking.
    Safe to call from sync or async contexts.
    """
    if not _enabled:
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            track_async(event_name, event_category, user_id, channel_id, guild_id, properties)
        )
    except RuntimeError:
        # No running loop - skip tracking
        pass


async def shutdown() -> None:
    """Close the connection pool. Call on bot shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
```

### Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ANALYTICS_ENABLED` | `true` | Set to `false` to disable tracking |
| `DATABASE_URL` | (required) | PostgreSQL connection string |

---

## Phase 3: Event Instrumentation

### Priority 1: Core Message Flow

| Event | Location | Properties |
|-------|----------|------------|
| `message_received` | `discord_bot.py:344` | `channel_type` (dm/guild), `has_attachments`, `content_length` |
| `response_sent` | `discord_bot.py:628` | `response_length`, `chunk_count`, `latency_ms` |

**Implementation:**
```python
# discord_bot.py - in on_message handler
from analytics import track

async def on_message(self, message: discord.Message):
    if message.author.bot:
        return

    channel_type = "dm" if isinstance(message.channel, discord.DMChannel) else "guild"
    track(
        "message_received", "message",
        user_id=message.author.id,
        channel_id=message.channel.id,
        guild_id=getattr(message.guild, "id", None),
        properties={
            "channel_type": channel_type,
            "has_attachments": len(message.attachments) > 0,
            "content_length": len(message.content),
        }
    )
    # ... rest of handler
```

### Priority 2: Memory System

| Event | Location | Properties |
|-------|----------|------------|
| `extraction_triggered` | `memory/manager.py:171` | `message_count`, `channel_privacy` |
| `memory_created` | `memory/manager.py:222` | `memory_type`, `privacy_level`, `confidence` |
| `memory_merged` | `memory/updater.py` | `original_id`, `merged_count` |
| `retrieval_performed` | `memory/manager.py:64` | `query_length`, `results_count`, `top_similarity` |

### Priority 3: Slash Commands

| Event | Location | Properties |
|-------|----------|------------|
| `command_used` | `memory_commands.py` (each command) | `command_name`, `subcommand`, `page` |

**Implementation:**
```python
# memory_commands.py - in each command handler
from analytics import track

@app_commands.command(name="list")
async def list_memories(self, interaction: discord.Interaction, ...):
    track(
        "command_used", "command",
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        guild_id=getattr(interaction.guild, "id", None),
        properties={"command_name": "memories", "subcommand": "list", "page": page}
    )
    # ... rest of handler
```

### Priority 4: API Usage

| Event | Location | Properties |
|-------|----------|------------|
| `claude_api_call` | `claude_client.py:440` | `model`, `input_tokens`, `output_tokens`, `cache_read`, `cache_creation`, `latency_ms` |
| `voyage_api_call` | `memory/retriever.py` | `model`, `input_length`, `latency_ms` |

### Priority 5: Tool Execution

| Event | Location | Properties |
|-------|----------|------------|
| `tool_executed` | `claude_client.py:477` | `tool_name`, `success`, `latency_ms` |

### Priority 6: Errors

| Event | Location | Properties |
|-------|----------|------------|
| `api_error` | `claude_client.py:634` | `error_type`, `error_message`, `context` |
| `extraction_failed` | `memory/manager.py:236` | `error_type`, `message_count` |

---

## Phase 4: Query Examples

### Daily Active Users
```sql
SELECT DATE(created_at) as day, COUNT(DISTINCT user_id) as dau
FROM analytics_events
WHERE event_name = 'message_received'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY day DESC;
```

### Messages by Channel Type
```sql
SELECT
    properties->>'channel_type' as channel_type,
    COUNT(*) as message_count
FROM analytics_events
WHERE event_name = 'message_received'
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY properties->>'channel_type';
```

### Token Usage Over Time
```sql
SELECT
    DATE(created_at) as day,
    SUM((properties->>'input_tokens')::int) as total_input,
    SUM((properties->>'output_tokens')::int) as total_output,
    SUM((properties->>'cache_read')::int) as cache_hits
FROM analytics_events
WHERE event_name = 'claude_api_call'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY day DESC;
```

### Estimated API Cost
```sql
SELECT
    DATE(created_at) as day,
    ROUND(
        (SUM((properties->>'input_tokens')::int) * 0.000003 +
         SUM((properties->>'output_tokens')::int) * 0.000015)::numeric,
        4
    ) as estimated_cost_usd
FROM analytics_events
WHERE event_name = 'claude_api_call'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY DATE(created_at)
ORDER BY day DESC;
```

### Memory System Effectiveness
```sql
-- Extraction success rate
SELECT
    COUNT(*) FILTER (WHERE event_name = 'memory_created') as memories_created,
    COUNT(*) FILTER (WHERE event_name = 'extraction_triggered') as extractions_triggered,
    ROUND(
        COUNT(*) FILTER (WHERE event_name = 'memory_created')::numeric /
        NULLIF(COUNT(*) FILTER (WHERE event_name = 'extraction_triggered'), 0),
        2
    ) as memories_per_extraction
FROM analytics_events
WHERE event_category = 'memory'
  AND created_at > NOW() - INTERVAL '7 days';
```

### Command Usage Breakdown
```sql
SELECT
    properties->>'subcommand' as command,
    COUNT(*) as usage_count,
    COUNT(DISTINCT user_id) as unique_users
FROM analytics_events
WHERE event_name = 'command_used'
  AND properties->>'command_name' = 'memories'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY properties->>'subcommand'
ORDER BY usage_count DESC;
```

### Error Rate by Type
```sql
SELECT
    properties->>'error_type' as error_type,
    COUNT(*) as occurrences,
    MAX(created_at) as last_occurrence
FROM analytics_events
WHERE event_category = 'error'
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY properties->>'error_type'
ORDER BY occurrences DESC;
```

### Response Latency Percentiles
```sql
SELECT
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY (properties->>'latency_ms')::int) as p50_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY (properties->>'latency_ms')::int) as p95_ms,
    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY (properties->>'latency_ms')::int) as p99_ms
FROM analytics_events
WHERE event_name = 'response_sent'
  AND created_at > NOW() - INTERVAL '24 hours';
```

---

## Phase 5: CLI Tool (Optional)

### File: `scripts/analytics_query.py`

```python
"""
CLI tool for querying analytics data.

Usage:
    python scripts/analytics_query.py dau              # Daily active users
    python scripts/analytics_query.py tokens           # Token usage
    python scripts/analytics_query.py commands         # Command usage
    python scripts/analytics_query.py errors           # Recent errors
    python scripts/analytics_query.py summary          # Overall summary
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

QUERIES = {
    "dau": """
        SELECT DATE(created_at) as day, COUNT(DISTINCT user_id) as users
        FROM analytics_events
        WHERE event_name = 'message_received' AND created_at > NOW() - INTERVAL '14 days'
        GROUP BY DATE(created_at) ORDER BY day DESC
    """,
    "tokens": """
        SELECT DATE(created_at) as day,
               SUM((properties->>'input_tokens')::int) as input,
               SUM((properties->>'output_tokens')::int) as output
        FROM analytics_events
        WHERE event_name = 'claude_api_call' AND created_at > NOW() - INTERVAL '14 days'
        GROUP BY DATE(created_at) ORDER BY day DESC
    """,
    "commands": """
        SELECT properties->>'subcommand' as cmd, COUNT(*) as count
        FROM analytics_events
        WHERE event_name = 'command_used' AND created_at > NOW() - INTERVAL '30 days'
        GROUP BY properties->>'subcommand' ORDER BY count DESC
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
            COUNT(*) FILTER (WHERE event_category = 'error') as errors
        FROM analytics_events
        WHERE created_at > NOW() - INTERVAL '24 hours'
    """
}


async def run_query(query_name: str):
    if query_name not in QUERIES:
        print(f"Unknown query: {query_name}")
        print(f"Available: {', '.join(QUERIES.keys())}")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(QUERIES[query_name])
        if not rows:
            print("No data found")
            return

        # Print header
        columns = list(rows[0].keys())
        print(" | ".join(f"{c:>12}" for c in columns))
        print("-" * (15 * len(columns)))

        # Print rows
        for row in rows:
            print(" | ".join(f"{str(v):>12}" for v in row.values()))
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analytics_query.py <query_name>")
        print(f"Available queries: {', '.join(QUERIES.keys())}")
        sys.exit(1)

    asyncio.run(run_query(sys.argv[1]))
```

---

## Phase 6: Admin Slash Commands

Expose analytics data to admins directly in Discord via `/analytics` commands. Owner-only access controlled by `OWNER_ID` environment variable.

### File: `src/commands/analytics_commands.py`

```python
"""
Analytics Slash Commands (Admin Only)

Discord slash commands for viewing bot analytics and usage metrics.
Restricted to bot owner via OWNER_ID environment variable.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("slashAI.commands.analytics")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


def owner_only():
    """Decorator to restrict commands to bot owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)


class AnalyticsCommands(commands.Cog):
    """
    Slash commands for viewing analytics (owner-only).

    Commands:
    - /analytics summary - 24-hour overview
    - /analytics dau - Daily active users
    - /analytics tokens - Token usage and costs
    - /analytics commands - Command usage breakdown
    - /analytics errors - Recent errors
    - /analytics users - Top users by activity
    """

    analytics_group = app_commands.Group(
        name="analytics",
        description="View bot analytics and usage metrics (owner only)",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool

    # =========================================================================
    # /analytics summary
    # =========================================================================

    @analytics_group.command(name="summary")
    @owner_only()
    @app_commands.describe(hours="Time range in hours (default: 24)")
    async def summary(self, interaction: discord.Interaction, hours: int = 24):
        """Quick overview of bot activity."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE event_name = 'message_received') as messages,
                COUNT(DISTINCT user_id) FILTER (WHERE event_name = 'message_received') as unique_users,
                COUNT(*) FILTER (WHERE event_category = 'memory') as memory_ops,
                COUNT(*) FILTER (WHERE event_name = 'memory_created') as memories_created,
                COUNT(*) FILTER (WHERE event_name = 'command_used') as commands_used,
                COUNT(*) FILTER (WHERE event_category = 'error') as errors,
                COALESCE(SUM((properties->>'input_tokens')::int) FILTER (WHERE event_name = 'claude_api_call'), 0) as input_tokens,
                COALESCE(SUM((properties->>'output_tokens')::int) FILTER (WHERE event_name = 'claude_api_call'), 0) as output_tokens
            FROM analytics_events
            WHERE created_at > NOW() - make_interval(hours => $1)
            """,
            hours,
        )

        # Calculate estimated cost (Sonnet 4.5 pricing)
        input_cost = (row["input_tokens"] or 0) * 0.000003
        output_cost = (row["output_tokens"] or 0) * 0.000015
        total_cost = input_cost + output_cost

        embed = discord.Embed(
            title=f"Analytics Summary ({hours}h)",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Messages", value=f"{row['messages']:,}", inline=True)
        embed.add_field(name="Unique Users", value=f"{row['unique_users']:,}", inline=True)
        embed.add_field(name="Commands Used", value=f"{row['commands_used']:,}", inline=True)
        embed.add_field(name="Memories Created", value=f"{row['memories_created']:,}", inline=True)
        embed.add_field(name="Memory Operations", value=f"{row['memory_ops']:,}", inline=True)
        embed.add_field(name="Errors", value=f"{row['errors']:,}", inline=True)
        embed.add_field(
            name="Tokens",
            value=f"In: {row['input_tokens']:,}\nOut: {row['output_tokens']:,}",
            inline=True,
        )
        embed.add_field(name="Est. Cost", value=f"${total_cost:.4f}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /analytics dau
    # =========================================================================

    @analytics_group.command(name="dau")
    @owner_only()
    @app_commands.describe(days="Number of days to show (default: 14)")
    async def dau(self, interaction: discord.Interaction, days: int = 14):
        """Daily active users over time."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT
                DATE(created_at) as day,
                COUNT(DISTINCT user_id) as users,
                COUNT(*) as messages
            FROM analytics_events
            WHERE event_name = 'message_received'
              AND created_at > NOW() - make_interval(days => $1)
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            """,
            days,
        )

        if not rows:
            await interaction.followup.send("No data available yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Daily Active Users ({days} days)",
            color=discord.Color.green(),
        )

        # Format as a simple table in description
        lines = ["```", "Date       | Users | Messages", "-" * 32]
        for row in rows:
            day_str = row["day"].strftime("%Y-%m-%d")
            lines.append(f"{day_str} | {row['users']:>5} | {row['messages']:>8}")
        lines.append("```")

        embed.description = "\n".join(lines)

        # Summary stats
        total_users = len(set(r["users"] for r in rows))
        avg_daily = sum(r["users"] for r in rows) / len(rows) if rows else 0
        embed.set_footer(text=f"Avg: {avg_daily:.1f} users/day")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /analytics tokens
    # =========================================================================

    @analytics_group.command(name="tokens")
    @owner_only()
    @app_commands.describe(days="Number of days to show (default: 14)")
    async def tokens(self, interaction: discord.Interaction, days: int = 14):
        """Token usage and estimated costs."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT
                DATE(created_at) as day,
                SUM((properties->>'input_tokens')::int) as input_tokens,
                SUM((properties->>'output_tokens')::int) as output_tokens,
                COALESCE(SUM((properties->>'cache_read')::int), 0) as cache_read,
                COUNT(*) as api_calls
            FROM analytics_events
            WHERE event_name = 'claude_api_call'
              AND created_at > NOW() - make_interval(days => $1)
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            """,
            days,
        )

        if not rows:
            await interaction.followup.send("No API call data available yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Token Usage ({days} days)",
            color=discord.Color.gold(),
        )

        lines = ["```", "Date       |    Input |   Output |    Cost", "-" * 44]
        total_cost = 0
        for row in rows:
            day_str = row["day"].strftime("%Y-%m-%d")
            cost = (row["input_tokens"] * 0.000003) + (row["output_tokens"] * 0.000015)
            total_cost += cost
            lines.append(f"{day_str} | {row['input_tokens']:>8} | {row['output_tokens']:>8} | ${cost:>6.3f}")
        lines.append("```")

        embed.description = "\n".join(lines)

        # Totals
        total_input = sum(r["input_tokens"] for r in rows)
        total_output = sum(r["output_tokens"] for r in rows)
        total_cache = sum(r["cache_read"] for r in rows)
        embed.add_field(name="Total Input", value=f"{total_input:,}", inline=True)
        embed.add_field(name="Total Output", value=f"{total_output:,}", inline=True)
        embed.add_field(name="Cache Hits", value=f"{total_cache:,}", inline=True)
        embed.set_footer(text=f"Total estimated cost: ${total_cost:.4f}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /analytics commands
    # =========================================================================

    @analytics_group.command(name="commands")
    @owner_only()
    @app_commands.describe(days="Number of days to analyze (default: 30)")
    async def commands_stats(self, interaction: discord.Interaction, days: int = 30):
        """Command usage breakdown."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT
                COALESCE(properties->>'command_name', 'unknown') as command_group,
                COALESCE(properties->>'subcommand', 'base') as subcommand,
                COUNT(*) as usage_count,
                COUNT(DISTINCT user_id) as unique_users
            FROM analytics_events
            WHERE event_name = 'command_used'
              AND created_at > NOW() - make_interval(days => $1)
            GROUP BY properties->>'command_name', properties->>'subcommand'
            ORDER BY usage_count DESC
            LIMIT 15
            """,
            days,
        )

        if not rows:
            await interaction.followup.send("No command usage data available yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Command Usage ({days} days)",
            color=discord.Color.purple(),
        )

        lines = ["```", "Command              | Uses | Users", "-" * 38]
        for row in rows:
            cmd = f"/{row['command_group']} {row['subcommand']}"[:20]
            lines.append(f"{cmd:<20} | {row['usage_count']:>4} | {row['unique_users']:>5}")
        lines.append("```")

        embed.description = "\n".join(lines)

        total_uses = sum(r["usage_count"] for r in rows)
        embed.set_footer(text=f"Total: {total_uses:,} command invocations")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /analytics errors
    # =========================================================================

    @analytics_group.command(name="errors")
    @owner_only()
    @app_commands.describe(limit="Number of errors to show (default: 10)")
    async def errors(self, interaction: discord.Interaction, limit: int = 10):
        """Recent errors."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT
                created_at,
                event_name,
                properties->>'error_type' as error_type,
                LEFT(properties->>'error_message', 100) as error_message
            FROM analytics_events
            WHERE event_category = 'error'
            ORDER BY created_at DESC
            LIMIT $1
            """,
            min(limit, 25),  # Cap at 25
        )

        if not rows:
            embed = discord.Embed(
                title="Recent Errors",
                description="No errors recorded.",
                color=discord.Color.green(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Recent Errors ({len(rows)})",
            color=discord.Color.red(),
        )

        for i, row in enumerate(rows[:10], 1):  # Show max 10 in fields
            timestamp = row["created_at"].strftime("%m/%d %H:%M")
            error_type = row["error_type"] or row["event_name"]
            message = row["error_message"] or "No message"
            embed.add_field(
                name=f"{i}. {error_type} ({timestamp})",
                value=message[:100],
                inline=False,
            )

        # Error summary by type
        type_counts = await self.db.fetch(
            """
            SELECT
                COALESCE(properties->>'error_type', event_name) as error_type,
                COUNT(*) as count
            FROM analytics_events
            WHERE event_category = 'error'
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY COALESCE(properties->>'error_type', event_name)
            ORDER BY count DESC
            LIMIT 5
            """,
        )

        if type_counts:
            summary = ", ".join(f"{r['error_type']}: {r['count']}" for r in type_counts)
            embed.set_footer(text=f"7-day breakdown: {summary}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /analytics users
    # =========================================================================

    @analytics_group.command(name="users")
    @owner_only()
    @app_commands.describe(days="Number of days to analyze (default: 30)")
    async def users(self, interaction: discord.Interaction, days: int = 30):
        """Top users by message count."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT
                user_id,
                COUNT(*) as message_count,
                COUNT(*) FILTER (WHERE properties->>'channel_type' = 'dm') as dm_count,
                MIN(created_at) as first_seen,
                MAX(created_at) as last_seen
            FROM analytics_events
            WHERE event_name = 'message_received'
              AND user_id IS NOT NULL
              AND created_at > NOW() - make_interval(days => $1)
            GROUP BY user_id
            ORDER BY message_count DESC
            LIMIT 10
            """,
            days,
        )

        if not rows:
            await interaction.followup.send("No user data available yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Top Users ({days} days)",
            color=discord.Color.blue(),
        )

        lines = ["```", "User ID            | Msgs |  DMs", "-" * 38]
        for row in rows:
            lines.append(f"{row['user_id']:<18} | {row['message_count']:>4} | {row['dm_count']:>4}")
        lines.append("```")

        embed.description = "\n".join(lines)

        # Try to resolve usernames for top 3
        resolved = []
        for row in rows[:3]:
            try:
                user = await self.bot.fetch_user(row["user_id"])
                resolved.append(f"{user.display_name}: {row['message_count']} msgs")
            except:
                resolved.append(f"User {row['user_id']}: {row['message_count']} msgs")

        if resolved:
            embed.add_field(name="Top 3", value="\n".join(resolved), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /analytics memory
    # =========================================================================

    @analytics_group.command(name="memory")
    @owner_only()
    @app_commands.describe(days="Number of days to analyze (default: 7)")
    async def memory_stats(self, interaction: discord.Interaction, days: int = 7):
        """Memory system statistics."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE event_name = 'extraction_triggered') as extractions,
                COUNT(*) FILTER (WHERE event_name = 'memory_created') as created,
                COUNT(*) FILTER (WHERE event_name = 'memory_merged') as merged,
                COUNT(*) FILTER (WHERE event_name = 'retrieval_performed') as retrievals,
                COUNT(*) FILTER (WHERE event_name = 'extraction_failed') as failures,
                AVG((properties->>'results_count')::float) FILTER (WHERE event_name = 'retrieval_performed') as avg_results,
                AVG((properties->>'top_similarity')::float) FILTER (WHERE event_name = 'retrieval_performed') as avg_similarity
            FROM analytics_events
            WHERE event_category = 'memory'
              AND created_at > NOW() - make_interval(days => $1)
            """,
            days,
        )

        embed = discord.Embed(
            title=f"Memory System Stats ({days} days)",
            color=discord.Color.teal(),
        )

        embed.add_field(name="Extractions Triggered", value=f"{row['extractions'] or 0:,}", inline=True)
        embed.add_field(name="Memories Created", value=f"{row['created'] or 0:,}", inline=True)
        embed.add_field(name="Memories Merged", value=f"{row['merged'] or 0:,}", inline=True)
        embed.add_field(name="Retrievals", value=f"{row['retrievals'] or 0:,}", inline=True)
        embed.add_field(name="Extraction Failures", value=f"{row['failures'] or 0:,}", inline=True)

        if row["avg_results"]:
            embed.add_field(name="Avg Results/Query", value=f"{row['avg_results']:.1f}", inline=True)
        if row["avg_similarity"]:
            embed.add_field(name="Avg Top Similarity", value=f"{row['avg_similarity']:.3f}", inline=True)

        # Success rate
        if row["extractions"] and row["extractions"] > 0:
            success_rate = ((row["extractions"] - (row["failures"] or 0)) / row["extractions"]) * 100
            embed.set_footer(text=f"Extraction success rate: {success_rate:.1f}%")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, db_pool: asyncpg.Pool):
    """Register the analytics commands cog."""
    await bot.add_cog(AnalyticsCommands(bot, db_pool))
```

### Command Summary

| Command | Description | Key Metrics |
|---------|-------------|-------------|
| `/analytics summary [hours]` | Quick overview | Messages, users, tokens, cost, errors |
| `/analytics dau [days]` | Daily active users | Users and messages per day |
| `/analytics tokens [days]` | Token usage | Input/output tokens, cache hits, costs |
| `/analytics commands [days]` | Command breakdown | Usage counts by command |
| `/analytics errors [limit]` | Recent errors | Error type, message, timestamp |
| `/analytics users [days]` | Top users | Message counts, DM vs guild |
| `/analytics memory [days]` | Memory system | Extractions, retrievals, success rate |

### Access Control

Commands are restricted to the bot owner using the `OWNER_ID` environment variable:

```python
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID
    return app_commands.check(predicate)
```

Non-owners receive: "This command is restricted to the bot owner."

### Registration

Add to `discord_bot.py` startup:

```python
from commands.analytics_commands import AnalyticsCommands

async def setup_hook(self):
    # ... existing setup ...

    # Register analytics commands (owner-only)
    if self.db_pool:
        await self.add_cog(AnalyticsCommands(self, self.db_pool))
```

### Embed Formatting

All responses use Discord embeds for clean presentation:

- **Color coding**: Blue (general), Green (success), Gold (costs), Red (errors), Purple (commands), Teal (memory)
- **Ephemeral**: All responses are private to the admin
- **Tables**: Fixed-width formatting using code blocks for tabular data
- **Timestamps**: Relative timestamps where appropriate
- **Footers**: Summary statistics and totals

---

## Implementation Checklist

### Database Setup
- [ ] Create migration file `migrations/009_create_analytics.sql`
- [ ] Run migration on production database
- [ ] Verify indexes are created

### Core Module
- [ ] Create `src/analytics.py`
- [ ] Add `ANALYTICS_ENABLED` to environment documentation
- [ ] Test connection pool initialization

### Instrumentation (by priority)
- [ ] **P1**: `message_received` in `discord_bot.py`
- [ ] **P1**: `response_sent` in `discord_bot.py`
- [ ] **P2**: Memory events in `memory/manager.py`
- [ ] **P3**: Command events in `memory_commands.py`
- [ ] **P4**: API call events in `claude_client.py`
- [ ] **P5**: Tool execution events in `claude_client.py`
- [ ] **P6**: Error events throughout

### CLI Tool (optional)
- [ ] Create `scripts/analytics_query.py`
- [ ] Document in README or CLAUDE.md

### Admin Slash Commands
- [ ] Create `src/commands/analytics_commands.py`
- [ ] Register cog in `discord_bot.py` setup
- [ ] Test all 7 commands with sample data
- [ ] Verify owner-only access control works

### Documentation
- [ ] Update CLAUDE.md with analytics section
- [ ] Add query examples to docs
- [ ] Document `/analytics` commands in README

---

## Data Retention

For long-term operation, consider:

1. **Automatic cleanup** - Delete events older than 90 days:
   ```sql
   DELETE FROM analytics_events WHERE created_at < NOW() - INTERVAL '90 days';
   ```

2. **Aggregation tables** - Create daily/weekly rollups for historical trends:
   ```sql
   CREATE TABLE analytics_daily_summary (
       day DATE PRIMARY KEY,
       messages INT,
       unique_users INT,
       total_input_tokens BIGINT,
       total_output_tokens BIGINT,
       memories_created INT,
       errors INT
   );
   ```

3. **Partitioning** - If data grows significantly, partition by month.

---

## Privacy Considerations

- **No message content** is stored - only metadata and counts
- **User IDs** are stored for aggregate analysis, not individual tracking
- **Properties** should never contain PII beyond what's necessary
- Consider adding a `/privacy analytics-opt-out` command if users request it

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-01-09 | Initial plan |
| 1.1 | 2025-01-09 | Added Phase 6: Admin Slash Commands for in-Discord analytics consumption |
