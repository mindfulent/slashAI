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
Analytics Slash Commands (Admin Only)

Discord slash commands for viewing bot analytics and usage metrics.
Restricted to bot owner via OWNER_ID environment variable.
"""

import logging
import os
from datetime import datetime

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
    - /analytics memory - Memory system stats
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
            except Exception:
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
