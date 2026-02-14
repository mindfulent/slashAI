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
SynthCraft Slash Commands (Owner Only)

Discord slash commands for viewing SynthCraft license, usage, and generation data.
Restricted to bot owner via OWNER_ID environment variable.
"""

import logging
import os
from datetime import datetime

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from utils.geoip import resolve_geo

logger = logging.getLogger("slashAI.commands.synthcraft")

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


def _display_name(row) -> str:
    """Return label if set, otherwise server_name, otherwise 'Unknown'."""
    return row.get("label") or row.get("server_name") or "Unknown"


class SynthCraftCommands(commands.Cog):
    """
    Slash commands for viewing SynthCraft data (owner-only).

    Commands:
    - /synthcraft licenses - List all licenses
    - /synthcraft stats - Aggregate generation statistics
    - /synthcraft servers [server_id] - Per-server usage summary
    - /synthcraft player <name_or_uuid> - Player generation lookup
    - /synthcraft active - Currently pending generations
    - /synthcraft hide <license_id> - Hide a license from default listings
    - /synthcraft unhide <license_id> - Unhide a license
    - /synthcraft label <license_id> [name] - Set or clear a display label
    """

    synthcraft_group = app_commands.Group(
        name="synthcraft",
        description="View SynthCraft license and usage data (owner only)",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool

    # =========================================================================
    # /synthcraft licenses
    # =========================================================================

    @synthcraft_group.command(name="licenses")
    @owner_only()
    @app_commands.describe(show_hidden="Include hidden licenses (default: False)")
    async def licenses(self, interaction: discord.Interaction, show_hidden: bool = False):
        """List all SynthCraft licenses."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT id, server_name, license_key, state, tier, credit_remaining,
                   last_validated, server_ip, hidden, label
            FROM synthcraft_licenses
            WHERE ($1 OR hidden = false)
            ORDER BY id ASC
            """,
            show_hidden,
        )

        if not rows:
            await interaction.followup.send("No SynthCraft licenses found.", ephemeral=True)
            return

        title = f"SynthCraft Licenses ({len(rows)})"
        if show_hidden:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        embed = discord.Embed(
            title=title,
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            key_preview = row["license_key"][:8] + "..." if row["license_key"] else "N/A"
            credit = f"${row['credit_remaining']:.2f}" if row["credit_remaining"] is not None else "N/A"
            validated = row["last_validated"].strftime("%Y-%m-%d %H:%M") if row["last_validated"] else "Never"
            ip = row["server_ip"] or "N/A"
            geo = geo_map.get(row["server_ip"], "")
            location = f" ({geo})" if geo else ""
            hidden_marker = " [HIDDEN]" if row["hidden"] else ""

            embed.add_field(
                name=f"#{row['id']} \u2014 {_display_name(row)} ({row['state']}){hidden_marker}",
                value=(
                    f"Key: `{key_preview}` | Tier: {row['tier'] or 'N/A'}\n"
                    f"IP: {ip}{location} | Credit: {credit} | Validated: {validated}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft stats
    # =========================================================================

    @synthcraft_group.command(name="stats")
    @owner_only()
    async def stats(self, interaction: discord.Interaction):
        """Aggregate SynthCraft generation statistics."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            """
            SELECT COUNT(*) as total_generations,
                   COALESCE(SUM(cost_usd), 0) as total_cost,
                   COUNT(DISTINCT license_id) as unique_servers,
                   COUNT(*) FILTER (WHERE status = 'completed') as completed,
                   COUNT(*) FILTER (WHERE status = 'failed') as failed,
                   COUNT(*) FILTER (WHERE status = 'pending') as pending,
                   COALESCE(SUM(duration_seconds) FILTER (WHERE status = 'completed'), 0) as total_seconds
            FROM synthcraft_generations
            """
        )

        embed = discord.Embed(
            title="SynthCraft Generation Stats",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )

        embed.add_field(name="Total Generations", value=f"{row['total_generations']:,}", inline=True)
        embed.add_field(name="Completed", value=f"{row['completed']:,}", inline=True)
        embed.add_field(name="Failed", value=f"{row['failed']:,}", inline=True)
        embed.add_field(name="Pending", value=f"{row['pending']:,}", inline=True)
        embed.add_field(name="Unique Servers", value=f"{row['unique_servers']:,}", inline=True)
        embed.add_field(
            name="Total Cost",
            value=f"${row['total_cost']:.4f}" if row["total_cost"] else "$0.00",
            inline=True,
        )

        total_mins = row["total_seconds"] / 60 if row["total_seconds"] else 0
        embed.add_field(name="Total Audio", value=f"{total_mins:.1f} min", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft servers
    # =========================================================================

    @synthcraft_group.command(name="servers")
    @owner_only()
    @app_commands.describe(
        server_id="Optional license ID to view details for a specific server",
        show_hidden="Include hidden servers (default: False)",
    )
    async def servers(self, interaction: discord.Interaction, server_id: int = None, show_hidden: bool = False):
        """Per-server SynthCraft usage summary."""
        await interaction.response.defer(ephemeral=True)

        if server_id:
            # Explicit lookup bypasses hidden filter
            rows = await self.db.fetch(
                """
                SELECT sl.id, sl.server_id AS sid, sl.server_name, sl.state, sl.tier,
                       sl.server_ip, sl.hidden, sl.label,
                       COUNT(sg.id) AS generations,
                       COALESCE(SUM(sg.cost_usd), 0) AS total_cost,
                       COALESCE(SUM(sg.duration_seconds) FILTER (WHERE sg.status = 'completed'), 0) AS total_seconds
                FROM synthcraft_licenses sl
                LEFT JOIN synthcraft_generations sg ON sg.license_id = sl.id
                WHERE sl.id = $1
                GROUP BY sl.id, sl.server_id, sl.server_name, sl.state, sl.tier,
                         sl.server_ip, sl.hidden, sl.label
                ORDER BY total_seconds DESC
                """,
                server_id,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT sl.id, sl.server_id AS sid, sl.server_name, sl.state, sl.tier,
                       sl.server_ip, sl.hidden, sl.label,
                       COUNT(sg.id) AS generations,
                       COALESCE(SUM(sg.cost_usd), 0) AS total_cost,
                       COALESCE(SUM(sg.duration_seconds) FILTER (WHERE sg.status = 'completed'), 0) AS total_seconds
                FROM synthcraft_licenses sl
                LEFT JOIN synthcraft_generations sg ON sg.license_id = sl.id
                WHERE ($1 OR sl.hidden = false)
                GROUP BY sl.id, sl.server_id, sl.server_name, sl.state, sl.tier,
                         sl.server_ip, sl.hidden, sl.label
                ORDER BY total_seconds DESC
                """,
                show_hidden,
            )

        if not rows:
            msg = f"No server found with ID `{server_id}`." if server_id else "No SynthCraft servers found."
            await interaction.followup.send(msg, ephemeral=True)
            return

        title = f"SynthCraft Servers ({len(rows)})"
        if show_hidden and not server_id:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        embed = discord.Embed(
            title=title,
            color=discord.Color.purple(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            total_mins = f"{row['total_seconds'] / 60:.1f}" if row["total_seconds"] else "0"
            cost = f"${row['total_cost']:.4f}" if row["total_cost"] else "$0.00"
            ip = row["server_ip"] or "N/A"
            geo = geo_map.get(row["server_ip"], "")
            location = f" ({geo})" if geo else ""
            hidden_marker = " [HIDDEN]" if row["hidden"] else ""
            embed.add_field(
                name=f"#{row['id']} \u2014 {_display_name(row)} ({row['state']}){hidden_marker}",
                value=(
                    f"Server ID: `{row['sid']}`\n"
                    f"IP: {ip}{location} | Tier: {row['tier'] or 'N/A'} | Generations: {row['generations']:,}\n"
                    f"Audio: {total_mins} min | Cost: {cost}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft player
    # =========================================================================

    @synthcraft_group.command(name="player")
    @owner_only()
    @app_commands.describe(name_or_uuid="Player name or UUID to look up")
    async def player(self, interaction: discord.Interaction, name_or_uuid: str):
        """Look up a player's SynthCraft generation history."""
        await interaction.response.defer(ephemeral=True)

        summary = await self.db.fetchrow(
            """
            SELECT player_name, player_uuid,
                   COUNT(*) AS generations,
                   COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                   COALESCE(SUM(cost_usd), 0) AS total_cost,
                   COALESCE(SUM(duration_seconds) FILTER (WHERE status = 'completed'), 0) AS total_seconds
            FROM synthcraft_generations
            WHERE player_name ILIKE $1 OR player_uuid = $1
            GROUP BY player_name, player_uuid
            """,
            name_or_uuid,
        )

        if not summary:
            await interaction.followup.send(
                f"No SynthCraft generations found for `{name_or_uuid}`.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"SynthCraft Player: {summary['player_name']}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="UUID", value=f"`{summary['player_uuid']}`", inline=False)
        embed.add_field(name="Total Generations", value=f"{summary['generations']:,}", inline=True)
        embed.add_field(name="Completed", value=f"{summary['completed']:,}", inline=True)
        embed.add_field(
            name="Total Cost",
            value=f"${summary['total_cost']:.4f}" if summary["total_cost"] else "$0.00",
            inline=True,
        )

        total_mins = summary["total_seconds"] / 60 if summary["total_seconds"] else 0
        embed.add_field(name="Total Audio", value=f"{total_mins:.1f} min", inline=True)

        # Recent generations
        recent = await self.db.fetch(
            """
            SELECT prompt, duration_seconds, status, cost_usd, created_at
            FROM synthcraft_generations
            WHERE player_name ILIKE $1 OR player_uuid = $1
            ORDER BY id ASC
            LIMIT 10
            """,
            name_or_uuid,
        )

        if recent:
            lines = ["```", "Date         | Dur  | Status    | Prompt", "-" * 52]
            for r in recent:
                date = r["created_at"].strftime("%m/%d %H:%M") if r["created_at"] else "?"
                dur = f"{r['duration_seconds']}s" if r["duration_seconds"] else "?"
                status = r["status"] or "?"
                prompt = (r["prompt"] or "")[:20]
                if len(r["prompt"] or "") > 20:
                    prompt += "..."
                lines.append(f"{date:<12} | {dur:>4} | {status:<9} | {prompt}")
            lines.append("```")
            embed.add_field(name="Recent Generations", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft active
    # =========================================================================

    @synthcraft_group.command(name="active")
    @owner_only()
    async def active(self, interaction: discord.Interaction):
        """Show currently pending SynthCraft generations."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT g.player_name, g.prompt, g.duration_seconds, g.created_at,
                   sl.server_name
            FROM synthcraft_generations g
            JOIN synthcraft_licenses sl ON g.license_id = sl.id
            WHERE g.status = 'pending'
            ORDER BY g.created_at ASC
            """
        )

        if not rows:
            embed = discord.Embed(
                title="SynthCraft Active Generations",
                description="No pending generations right now.",
                color=discord.Color.greyple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"SynthCraft Active Generations ({len(rows)})",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            started = row["created_at"].strftime("%H:%M:%S") if row["created_at"] else "?"
            dur = f"{row['duration_seconds']}s" if row["duration_seconds"] else "?"
            prompt = (row["prompt"] or "")[:80]
            if len(row["prompt"] or "") > 80:
                prompt += "..."
            embed.add_field(
                name=f"{row['player_name']} on {row['server_name'] or 'Unknown'}",
                value=f"Started: {started} | Duration: {dur}\nPrompt: {prompt}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft hide
    # =========================================================================

    @synthcraft_group.command(name="hide")
    @owner_only()
    @app_commands.describe(license_id="License ID to hide")
    async def hide(self, interaction: discord.Interaction, license_id: int):
        """Hide a license from default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM synthcraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        if row["hidden"]:
            await interaction.followup.send(
                f"License #{row['id']} ({_display_name(row)}) is already hidden.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            "UPDATE synthcraft_licenses SET hidden = true, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Hidden", color=discord.Color.dark_grey())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft unhide
    # =========================================================================

    @synthcraft_group.command(name="unhide")
    @owner_only()
    @app_commands.describe(license_id="License ID to unhide")
    async def unhide(self, interaction: discord.Interaction, license_id: int):
        """Unhide a license so it appears in default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM synthcraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        if not row["hidden"]:
            await interaction.followup.send(
                f"License #{row['id']} ({_display_name(row)}) is not hidden.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            "UPDATE synthcraft_licenses SET hidden = false, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Unhidden", color=discord.Color.green())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /synthcraft label
    # =========================================================================

    @synthcraft_group.command(name="label")
    @owner_only()
    @app_commands.describe(
        license_id="License ID to label",
        name="Display label (omit to clear)",
    )
    async def label(self, interaction: discord.Interaction, license_id: int, name: str = None):
        """Set or clear a display label for a license."""
        await interaction.response.defer(ephemeral=True)

        if name and len(name) > 100:
            await interaction.followup.send(
                "Label must be 100 characters or fewer.", ephemeral=True
            )
            return

        row = await self.db.fetchrow(
            "SELECT id, server_name, label FROM synthcraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        old_display = _display_name(row)

        await self.db.execute(
            "UPDATE synthcraft_licenses SET label = $2, updated_at = NOW() WHERE id = $1",
            license_id,
            name,
        )

        if name:
            embed = discord.Embed(title="License Labeled", color=discord.Color.blue())
            embed.add_field(name="License", value=f"#{row['id']}", inline=True)
            embed.add_field(name="Old Name", value=old_display, inline=True)
            embed.add_field(name="New Label", value=name, inline=True)
        else:
            embed = discord.Embed(title="Label Cleared", color=discord.Color.light_grey())
            embed.add_field(name="License", value=f"#{row['id']}", inline=True)
            embed.add_field(name="Reverted To", value=row["server_name"] or "Unknown", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, db_pool: asyncpg.Pool):
    """Register the SynthCraft commands cog."""
    await bot.add_cog(SynthCraftCommands(bot, db_pool))
