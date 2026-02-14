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
StreamCraft Slash Commands (Owner Only)

Discord slash commands for viewing StreamCraft license, usage, and streaming data.
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

logger = logging.getLogger("slashAI.commands.streamcraft")

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


class StreamCraftCommands(commands.Cog):
    """
    Slash commands for viewing StreamCraft data (owner-only).

    Commands:
    - /streamcraft licenses - List all licenses
    - /streamcraft player <name_or_uuid> - Player usage lookup
    - /streamcraft servers - Per-server usage summary
    - /streamcraft active - Currently active rooms and participants
    - /streamcraft hide <license_id> - Hide a license from default listings
    - /streamcraft unhide <license_id> - Unhide a license
    - /streamcraft label <license_id> [name] - Set or clear a display label
    """

    streamcraft_group = app_commands.Group(
        name="streamcraft",
        description="View StreamCraft license and usage data (owner only)",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool

    # =========================================================================
    # /streamcraft licenses
    # =========================================================================

    @streamcraft_group.command(name="licenses")
    @owner_only()
    @app_commands.describe(show_hidden="Include hidden licenses (default: False)")
    async def licenses(self, interaction: discord.Interaction, show_hidden: bool = False):
        """List all StreamCraft licenses."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT id, server_name, license_key, state, tier, credit_remaining,
                   last_validated, server_ip, hidden, label, activated_by_name
            FROM streamcraft_licenses
            WHERE ($1 OR hidden = false)
            ORDER BY id ASC
            """,
            show_hidden,
        )

        if not rows:
            await interaction.followup.send("No StreamCraft licenses found.", ephemeral=True)
            return

        title = f"StreamCraft Licenses ({len(rows)})"
        if show_hidden:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        embed = discord.Embed(
            title=title,
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:  # Discord embed field limit
            key_preview = row["license_key"][:8] + "..." if row["license_key"] else "N/A"
            credit = f"${row['credit_remaining']:.2f}" if row["credit_remaining"] is not None else "N/A"
            validated = row["last_validated"].strftime("%Y-%m-%d %H:%M") if row["last_validated"] else "Never"
            ip = row["server_ip"] or "N/A"
            geo = geo_map.get(row["server_ip"], "")
            location = f" ({geo})" if geo else ""
            hidden_marker = " [HIDDEN]" if row["hidden"] else ""

            activated = f"\nActivated by: {row['activated_by_name']}" if row.get("activated_by_name") else ""

            embed.add_field(
                name=f"#{row['id']} \u2014 {_display_name(row)} ({row['state']}){hidden_marker}",
                value=(
                    f"Key: `{key_preview}` | Tier: {row['tier'] or 'N/A'}\n"
                    f"IP: {ip}{location} | Credit: {credit} | Validated: {validated}{activated}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft player
    # =========================================================================

    @streamcraft_group.command(name="player")
    @owner_only()
    @app_commands.describe(name_or_uuid="Player name or UUID to look up")
    async def player(self, interaction: discord.Interaction, name_or_uuid: str):
        """Look up a player's StreamCraft usage."""
        await interaction.response.defer(ephemeral=True)

        # Aggregate stats
        summary = await self.db.fetchrow(
            """
            SELECT player_name, player_uuid,
                   SUM(minutes_used) as total_minutes,
                   SUM(cost_usd) as total_cost,
                   COUNT(*) as sessions
            FROM streamcraft_usage
            WHERE player_name ILIKE $1 OR player_uuid = $1
            GROUP BY player_name, player_uuid
            """,
            name_or_uuid,
        )

        if not summary:
            await interaction.followup.send(
                f"No StreamCraft usage found for `{name_or_uuid}`.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"StreamCraft Player: {summary['player_name']}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="UUID", value=f"`{summary['player_uuid']}`", inline=False)
        embed.add_field(name="Total Sessions", value=f"{summary['sessions']:,}", inline=True)
        embed.add_field(
            name="Total Minutes",
            value=f"{summary['total_minutes']:.1f}" if summary["total_minutes"] else "0",
            inline=True,
        )
        embed.add_field(
            name="Total Cost",
            value=f"${summary['total_cost']:.4f}" if summary["total_cost"] else "$0.00",
            inline=True,
        )

        # Recent sessions
        sessions = await self.db.fetch(
            """
            SELECT session_start, session_end, minutes_used, cost_usd
            FROM streamcraft_usage
            WHERE player_name ILIKE $1 OR player_uuid = $1
            ORDER BY session_start DESC
            LIMIT 10
            """,
            name_or_uuid,
        )

        if sessions:
            lines = ["```", "Start            | Min  | Cost", "-" * 36]
            for s in sessions:
                start = s["session_start"].strftime("%m/%d %H:%M") if s["session_start"] else "?"
                mins = f"{s['minutes_used']:.1f}" if s["minutes_used"] else "0.0"
                cost = f"${s['cost_usd']:.4f}" if s["cost_usd"] else "$0.00"
                active = " *" if s["session_end"] is None else ""
                lines.append(f"{start:<16} | {mins:>4} | {cost}{active}")
            lines.append("```")
            if any(s["session_end"] is None for s in sessions):
                lines.append("\\* = active session")
            embed.add_field(name="Recent Sessions", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft servers
    # =========================================================================

    @streamcraft_group.command(name="servers")
    @owner_only()
    @app_commands.describe(
        server_id="Optional license ID to view details for a specific server",
        show_hidden="Include hidden servers (default: False)",
    )
    async def servers(self, interaction: discord.Interaction, server_id: int = None, show_hidden: bool = False):
        """Per-server StreamCraft usage summary."""
        await interaction.response.defer(ephemeral=True)

        if server_id:
            # Explicit lookup bypasses hidden filter
            rows = await self.db.fetch(
                """
                SELECT sl.id, sl.server_id as sid, sl.server_name, sl.state, sl.tier,
                       sl.server_ip, sl.hidden, sl.label, sl.activated_by_name,
                       COUNT(su.id) as sessions,
                       COALESCE(SUM(su.minutes_used), 0) as total_minutes,
                       COALESCE(SUM(su.cost_usd), 0) as total_cost
                FROM streamcraft_licenses sl
                LEFT JOIN streamcraft_usage su ON su.license_id = sl.id
                WHERE sl.id = $1
                GROUP BY sl.id, sl.server_id, sl.server_name, sl.state, sl.tier,
                         sl.server_ip, sl.hidden, sl.label, sl.activated_by_name
                ORDER BY total_minutes DESC
                """,
                server_id,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT sl.id, sl.server_id as sid, sl.server_name, sl.state, sl.tier,
                       sl.server_ip, sl.hidden, sl.label, sl.activated_by_name,
                       COUNT(su.id) as sessions,
                       COALESCE(SUM(su.minutes_used), 0) as total_minutes,
                       COALESCE(SUM(su.cost_usd), 0) as total_cost
                FROM streamcraft_licenses sl
                LEFT JOIN streamcraft_usage su ON su.license_id = sl.id
                WHERE ($1 OR sl.hidden = false)
                GROUP BY sl.id, sl.server_id, sl.server_name, sl.state, sl.tier,
                         sl.server_ip, sl.hidden, sl.label, sl.activated_by_name
                ORDER BY total_minutes DESC
                """,
                show_hidden,
            )

        if not rows:
            msg = f"No server found with ID `{server_id}`." if server_id else "No StreamCraft servers found."
            await interaction.followup.send(msg, ephemeral=True)
            return

        title = f"StreamCraft Servers ({len(rows)})"
        if show_hidden and not server_id:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        embed = discord.Embed(
            title=title,
            color=discord.Color.purple(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            mins = f"{row['total_minutes']:.1f}" if row["total_minutes"] else "0"
            cost = f"${row['total_cost']:.4f}" if row["total_cost"] else "$0.00"
            ip = row["server_ip"] or "N/A"
            geo = geo_map.get(row["server_ip"], "")
            location = f" ({geo})" if geo else ""
            hidden_marker = " [HIDDEN]" if row["hidden"] else ""
            activated = f"\nActivated by: {row['activated_by_name']}" if row.get("activated_by_name") else ""
            embed.add_field(
                name=f"#{row['id']} \u2014 {_display_name(row)} ({row['state']}){hidden_marker}",
                value=(
                    f"Server ID: `{row['sid']}`\n"
                    f"IP: {ip}{location} | Tier: {row['tier'] or 'N/A'} | Sessions: {row['sessions']:,}\n"
                    f"Minutes: {mins} | Cost: {cost}{activated}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft active
    # =========================================================================

    @streamcraft_group.command(name="active")
    @owner_only()
    async def active(self, interaction: discord.Interaction):
        """Show currently active StreamCraft rooms and participants."""
        await interaction.response.defer(ephemeral=True)

        rooms = await self.db.fetch(
            """
            SELECT sr.room_name, sr.participant_count, sr.last_active,
                   su.player_name, su.session_start
            FROM streamcraft_rooms sr
            LEFT JOIN streamcraft_usage su ON su.session_end IS NULL
              AND su.license_id = (
                  SELECT id FROM streamcraft_licenses WHERE server_id = sr.server_id
              )
            WHERE sr.participant_count > 0
            ORDER BY sr.last_active DESC
            """
        )

        if not rooms:
            embed = discord.Embed(
                title="StreamCraft Active Streams",
                description="No active streams right now.",
                color=discord.Color.greyple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="StreamCraft Active Streams",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow(),
        )

        # Group by room
        room_map = {}
        for row in rooms:
            name = row["room_name"] or "Unknown Room"
            if name not in room_map:
                room_map[name] = {
                    "participant_count": row["participant_count"],
                    "last_active": row["last_active"],
                    "players": [],
                }
            if row["player_name"]:
                since = row["session_start"].strftime("%H:%M") if row["session_start"] else "?"
                room_map[name]["players"].append(f"{row['player_name']} (since {since})")

        for room_name, info in room_map.items():
            last = info["last_active"].strftime("%H:%M:%S") if info["last_active"] else "?"
            players = "\n".join(info["players"]) if info["players"] else "No player data"
            embed.add_field(
                name=f"{room_name} ({info['participant_count']} participants)",
                value=f"Last active: {last}\n{players}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft activate
    # =========================================================================

    @streamcraft_group.command(name="activate")
    @owner_only()
    @app_commands.describe(license_id="License ID to activate")
    async def activate(self, interaction: discord.Interaction, license_id: int):
        """Activate a StreamCraft license (set to ACTIVE/unlimited) by license ID."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, state, tier, credit_remaining, server_ip "
            "FROM streamcraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        if row["state"] == "ACTIVE" and row["tier"] == "standard":
            await interaction.followup.send(
                f"License #{row['id']} ({row['server_name']}) is already ACTIVE.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            """UPDATE streamcraft_licenses
               SET state = 'ACTIVE', tier = 'standard',
                   credit_remaining = NULL, expires_at = NULL,
                   updated_at = NOW()
               WHERE id = $1""",
            row["id"],
        )

        embed = discord.Embed(title="License Activated", color=discord.Color.green())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=row["server_name"] or "Unknown", inline=True)
        embed.add_field(name="IP", value=row["server_ip"] or "N/A", inline=True)
        embed.add_field(
            name="Before", value=f"{row['state']} / {row['tier']}", inline=True
        )
        embed.add_field(name="After", value="ACTIVE / standard (unlimited)", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft deactivate
    # =========================================================================

    @streamcraft_group.command(name="deactivate")
    @owner_only()
    @app_commands.describe(license_id="License ID to deactivate")
    async def deactivate(self, interaction: discord.Interaction, license_id: int):
        """Deactivate a StreamCraft license (set to EXPIRED) by license ID."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, state, tier, server_ip "
            "FROM streamcraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        if row["state"] == "EXPIRED":
            await interaction.followup.send(
                f"License #{row['id']} ({row['server_name']}) is already EXPIRED.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            """UPDATE streamcraft_licenses
               SET state = 'EXPIRED', updated_at = NOW()
               WHERE id = $1""",
            row["id"],
        )

        embed = discord.Embed(title="License Deactivated", color=discord.Color.red())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=row["server_name"] or "Unknown", inline=True)
        embed.add_field(name="IP", value=row["server_ip"] or "N/A", inline=True)
        embed.add_field(
            name="Before", value=f"{row['state']} / {row['tier']}", inline=True
        )
        embed.add_field(name="After", value="EXPIRED", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft hide
    # =========================================================================

    @streamcraft_group.command(name="hide")
    @owner_only()
    @app_commands.describe(license_id="License ID to hide")
    async def hide(self, interaction: discord.Interaction, license_id: int):
        """Hide a license from default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM streamcraft_licenses WHERE id = $1",
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
            "UPDATE streamcraft_licenses SET hidden = true, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Hidden", color=discord.Color.dark_grey())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft unhide
    # =========================================================================

    @streamcraft_group.command(name="unhide")
    @owner_only()
    @app_commands.describe(license_id="License ID to unhide")
    async def unhide(self, interaction: discord.Interaction, license_id: int):
        """Unhide a license so it appears in default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM streamcraft_licenses WHERE id = $1",
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
            "UPDATE streamcraft_licenses SET hidden = false, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Unhidden", color=discord.Color.green())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /streamcraft label
    # =========================================================================

    @streamcraft_group.command(name="label")
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
            "SELECT id, server_name, label FROM streamcraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        old_display = _display_name(row)

        await self.db.execute(
            "UPDATE streamcraft_licenses SET label = $2, updated_at = NOW() WHERE id = $1",
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
    """Register the StreamCraft commands cog."""
    await bot.add_cog(StreamCraftCommands(bot, db_pool))
