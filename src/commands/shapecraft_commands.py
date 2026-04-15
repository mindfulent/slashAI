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
ShapeCraft Slash Commands (Owner Only)

Discord slash commands for viewing ShapeCraft license, usage, and generation data.
Restricted to bot owner via OWNER_ID environment variable.
"""

import logging
import os
from datetime import datetime
from itertools import groupby

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from commands.views import PaginationView, paginate_lines
from utils.geoip import resolve_geo

logger = logging.getLogger("slashAI.commands.shapecraft")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

_STATE_EMOJI = {"EXPIRED": "\U0001f534", "GRACE": "\U0001f7e1", "ACTIVE": "\U0001f7e2", "TRIAL": "\U0001f535", "NO_USAGE": "\u26aa"}

COST_PER_GEN = 0.025  # estimated cost per generation


def _status_color(rows) -> discord.Color:
    """Pick embed color based on the most urgent state present."""
    states = {r["state"] for r in rows}
    if "EXPIRED" in states:
        return discord.Color.red()
    if "GRACE" in states:
        return discord.Color.orange()
    return discord.Color.green()


def _compact_label(row, geo_map: dict) -> str:
    """Return a compact name for a server: label + geo, or just one."""
    label = row.get("label")
    geo = geo_map.get(row.get("server_ip", ""), "")
    if label and geo:
        return f"{label} \u00b7 {geo}"
    if label:
        return label
    if geo:
        return geo
    return row.get("server_name") or "Unknown"


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


class ShapeCraftCommands(commands.Cog):
    """
    Slash commands for viewing ShapeCraft data (owner-only).

    Commands:
    - /shapecraft licenses - List all licenses
    - /shapecraft stats - Aggregate generation statistics
    - /shapecraft servers [server_id] - Per-server usage summary
    - /shapecraft player <name_or_uuid> - Player generation lookup
    - /shapecraft active - Recently completed generations
    - /shapecraft set-state <license_id> <state> - Set license state (TRIAL/ACTIVE/GRACE/EXPIRED)
    - /shapecraft hide <license_id> - Hide a license from default listings
    - /shapecraft unhide <license_id> - Unhide a license
    - /shapecraft label <license_id> [name] - Set or clear a display label
    """

    shapecraft_group = app_commands.Group(
        name="shapecraft",
        description="View ShapeCraft license and usage data (owner only)",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool

    # =========================================================================
    # /shapecraft licenses
    # =========================================================================

    @shapecraft_group.command(name="licenses")
    @owner_only()
    @app_commands.describe(show_hidden="Include hidden licenses (default: False)")
    async def licenses(self, interaction: discord.Interaction, show_hidden: bool = False):
        """List all ShapeCraft licenses."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT id, server_name, license_key, state, tier, trial_remaining,
                   monthly_used, last_validated, server_ip, hidden, label, activated_by_name
            FROM shapecraft_licenses
            WHERE ($1 OR hidden = false)
            ORDER BY CASE state WHEN 'EXPIRED' THEN 1 WHEN 'GRACE' THEN 2 WHEN 'ACTIVE' THEN 3 WHEN 'TRIAL' THEN 4 ELSE 5 END, id ASC
            """,
            show_hidden,
        )

        if not rows:
            await interaction.followup.send("No ShapeCraft licenses found.", ephemeral=True)
            return

        title = f"ShapeCraft Licenses ({len(rows)})"
        if show_hidden:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        lines: list[str] = []
        for state, group in groupby(rows, key=lambda r: r["state"]):
            state_rows = list(group)
            emoji = _STATE_EMOJI.get(state, "\u2796")
            lines.append(f"\n{emoji} **{state}** ({len(state_rows)})")
            for row in state_rows:
                name = _compact_label(row, geo_map)
                hidden = " \U0001f6ab" if row["hidden"] else ""
                parts = [f"**#{row['id']}** {name}{hidden}"]
                parts.append(f"`{row['tier'] or 'N/A'}`")
                if row["state"] == "TRIAL":
                    parts.append(f"{row['trial_remaining']} remaining")
                else:
                    parts.append(f"{row['monthly_used']} used this month")
                if row.get("activated_by_name"):
                    parts.append(row["activated_by_name"])
                lines.append(" \u00b7 ".join(parts))

        color = _status_color(rows)
        pages = paginate_lines(lines)

        def _make_embed(page_idx: int) -> discord.Embed:
            page_title = title if len(pages) == 1 else f"{title} (p{page_idx + 1}/{len(pages)})"
            return discord.Embed(
                title=page_title,
                description=pages[page_idx],
                color=color,
                timestamp=datetime.utcnow(),
            )

        embed = _make_embed(0)

        if len(pages) > 1:
            async def fetch_page(page_num: int) -> discord.Embed:
                return _make_embed(page_num - 1)

            view = PaginationView(
                user_id=interaction.user.id,
                current_page=1,
                total_pages=len(pages),
                fetch_page=fetch_page,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft stats
    # =========================================================================

    @shapecraft_group.command(name="stats")
    @owner_only()
    async def stats(self, interaction: discord.Interaction):
        """Aggregate ShapeCraft generation statistics."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            """
            SELECT COUNT(*) as total_generations,
                   COUNT(DISTINCT license_id) as unique_servers,
                   COUNT(DISTINCT player_uuid) as unique_players,
                   COUNT(*) FILTER (WHERE status = 'completed') as completed,
                   COUNT(*) FILTER (WHERE status = 'failed') as failed,
                   COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                   COALESCE(SUM(output_tokens), 0) as total_output_tokens
            FROM shapecraft_generations
            """
        )

        embed = discord.Embed(
            title="ShapeCraft Generation Stats",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow(),
        )

        embed.add_field(name="Total Generations", value=f"{row['total_generations']:,}", inline=True)
        embed.add_field(name="Completed", value=f"{row['completed']:,}", inline=True)
        embed.add_field(name="Failed", value=f"{row['failed']:,}", inline=True)
        embed.add_field(name="Unique Servers", value=f"{row['unique_servers']:,}", inline=True)
        embed.add_field(name="Unique Players", value=f"{row['unique_players']:,}", inline=True)

        est_cost = row["total_generations"] * COST_PER_GEN
        embed.add_field(name="Est. Cost", value=f"${est_cost:.2f}", inline=True)

        total_tokens = row["total_input_tokens"] + row["total_output_tokens"]
        embed.add_field(name="Total Tokens", value=f"{total_tokens:,}", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft servers
    # =========================================================================

    @shapecraft_group.command(name="servers")
    @owner_only()
    @app_commands.describe(
        server_id="Optional license ID to view details for a specific server",
        show_hidden="Include hidden servers (default: False)",
    )
    async def servers(self, interaction: discord.Interaction, server_id: int = None, show_hidden: bool = False):
        """Per-server ShapeCraft usage summary."""
        await interaction.response.defer(ephemeral=True)

        if server_id:
            rows = await self.db.fetch(
                """
                SELECT sl.id, sl.server_id AS sid, sl.server_name, sl.state, sl.tier,
                       sl.server_ip, sl.hidden, sl.label, sl.activated_by_name,
                       sl.trial_remaining, sl.monthly_used,
                       COUNT(sg.id) AS generations,
                       COUNT(DISTINCT sg.player_uuid) AS unique_players
                FROM shapecraft_licenses sl
                LEFT JOIN shapecraft_generations sg ON sg.license_id = sl.id
                WHERE sl.id = $1
                GROUP BY sl.id, sl.server_id, sl.server_name, sl.state, sl.tier,
                         sl.server_ip, sl.hidden, sl.label, sl.activated_by_name,
                         sl.trial_remaining, sl.monthly_used
                """,
                server_id,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT sl.id, sl.server_id AS sid, sl.server_name, sl.state, sl.tier,
                       sl.server_ip, sl.hidden, sl.label, sl.activated_by_name,
                       sl.trial_remaining, sl.monthly_used,
                       COUNT(sg.id) AS generations,
                       COUNT(DISTINCT sg.player_uuid) AS unique_players
                FROM shapecraft_licenses sl
                LEFT JOIN shapecraft_generations sg ON sg.license_id = sl.id
                WHERE ($1 OR sl.hidden = false)
                GROUP BY sl.id, sl.server_id, sl.server_name, sl.state, sl.tier,
                         sl.server_ip, sl.hidden, sl.label, sl.activated_by_name,
                         sl.trial_remaining, sl.monthly_used
                ORDER BY CASE sl.state WHEN 'EXPIRED' THEN 1 WHEN 'GRACE' THEN 2 WHEN 'ACTIVE' THEN 3 WHEN 'TRIAL' THEN 4 ELSE 5 END, generations DESC
                """,
                show_hidden,
            )

        if not rows:
            msg = f"No server found with ID `{server_id}`." if server_id else "No ShapeCraft servers found."
            await interaction.followup.send(msg, ephemeral=True)
            return

        title = f"ShapeCraft Servers ({len(rows)})"
        if show_hidden and not server_id:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        # Split TRIAL rows into used / no-usage buckets
        no_usage_rows: list = []
        display_rows: list = []
        for row in rows:
            if row["state"] == "TRIAL" and not (row["generations"] or 0):
                no_usage_rows.append(row)
            else:
                display_rows.append(row)
        no_usage_rows.sort(key=lambda r: r["id"], reverse=True)

        lines: list[str] = []
        for state, group in groupby(display_rows, key=lambda r: r["state"]):
            state_rows = list(group)
            emoji = _STATE_EMOJI.get(state, "\u2796")
            lines.append(f"\n{emoji} **{state}** ({len(state_rows)})")
            for row in state_rows:
                name = _compact_label(row, geo_map)
                hidden = " \U0001f6ab" if row["hidden"] else ""
                parts = [f"**#{row['id']}** {name}{hidden}"]
                parts.append(f"`{row['tier'] or 'N/A'}`")
                gens = row["generations"] or 0
                if gens:
                    parts.append(f"{gens:,} gen")
                    parts.append(f"{row['unique_players']} players")
                if row.get("activated_by_name"):
                    parts.append(row["activated_by_name"])
                lines.append(" \u00b7 ".join(parts))

        if no_usage_rows:
            emoji = _STATE_EMOJI["NO_USAGE"]
            lines.append(f"\n{emoji} **NO USAGE** ({len(no_usage_rows)})")
            for row in no_usage_rows:
                name = _compact_label(row, geo_map)
                hidden = " \U0001f6ab" if row["hidden"] else ""
                parts = [f"**#{row['id']}** {name}{hidden}"]
                parts.append(f"`{row['tier'] or 'N/A'}`")
                if row.get("activated_by_name"):
                    parts.append(row["activated_by_name"])
                lines.append(" \u00b7 ".join(parts))

        color = _status_color(rows)
        pages = paginate_lines(lines)

        def _make_embed(page_idx: int) -> discord.Embed:
            page_title = title if len(pages) == 1 else f"{title} (p{page_idx + 1}/{len(pages)})"
            return discord.Embed(
                title=page_title,
                description=pages[page_idx],
                color=color,
                timestamp=datetime.utcnow(),
            )

        embed = _make_embed(0)

        if len(pages) > 1:
            async def fetch_page(page_num: int) -> discord.Embed:
                return _make_embed(page_num - 1)

            view = PaginationView(
                user_id=interaction.user.id,
                current_page=1,
                total_pages=len(pages),
                fetch_page=fetch_page,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft player
    # =========================================================================

    @shapecraft_group.command(name="player")
    @owner_only()
    @app_commands.describe(name_or_uuid="Player name or UUID to look up")
    async def player(self, interaction: discord.Interaction, name_or_uuid: str):
        """Look up a player's ShapeCraft generation history."""
        await interaction.response.defer(ephemeral=True)

        summary = await self.db.fetchrow(
            """
            SELECT player_name, player_uuid,
                   COUNT(*) AS generations,
                   COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                   COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM shapecraft_generations
            WHERE player_name ILIKE $1 OR player_uuid = $1
            GROUP BY player_name, player_uuid
            """,
            name_or_uuid,
        )

        if not summary:
            await interaction.followup.send(
                f"No ShapeCraft generations found for `{name_or_uuid}`.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"ShapeCraft Player: {summary['player_name']}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="UUID", value=f"`{summary['player_uuid']}`", inline=False)
        embed.add_field(name="Total Generations", value=f"{summary['generations']:,}", inline=True)
        embed.add_field(name="Completed", value=f"{summary['completed']:,}", inline=True)
        embed.add_field(name="Failed", value=f"{summary['failed']:,}", inline=True)

        est_cost = summary["generations"] * COST_PER_GEN
        embed.add_field(name="Est. Cost", value=f"${est_cost:.2f}", inline=True)

        # Recent generations
        recent = await self.db.fetch(
            """
            SELECT description, display_name, status, created_at
            FROM shapecraft_generations
            WHERE player_name ILIKE $1 OR player_uuid = $1
            ORDER BY id ASC
            LIMIT 10
            """,
            name_or_uuid,
        )

        if recent:
            lines = ["```", "Date         | Status    | Description", "-" * 52]
            for r in recent:
                date = r["created_at"].strftime("%m/%d %H:%M") if r["created_at"] else "?"
                status = r["status"] or "?"
                desc = (r["description"] or "")[:22]
                if len(r["description"] or "") > 22:
                    desc += "..."
                lines.append(f"{date:<12} | {status:<9} | {desc}")
            lines.append("```")
            embed.add_field(name="Recent Generations", value="\n".join(lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft active
    # =========================================================================

    @shapecraft_group.command(name="active")
    @owner_only()
    async def active(self, interaction: discord.Interaction):
        """Show recent ShapeCraft generations."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT g.player_name, g.description, g.display_name, g.status, g.created_at,
                   sl.server_name
            FROM shapecraft_generations g
            JOIN shapecraft_licenses sl ON g.license_id = sl.id
            ORDER BY g.created_at DESC
            LIMIT 15
            """
        )

        if not rows:
            embed = discord.Embed(
                title="ShapeCraft Recent Generations",
                description="No generations yet.",
                color=discord.Color.greyple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"ShapeCraft Recent Generations ({len(rows)})",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            created = row["created_at"].strftime("%H:%M:%S") if row["created_at"] else "?"
            desc = (row["description"] or "")[:80]
            if len(row["description"] or "") > 80:
                desc += "..."
            display = row["display_name"] or "N/A"
            status_icon = "\u2705" if row["status"] == "completed" else "\u274c"
            embed.add_field(
                name=f"{status_icon} {row['player_name']} on {row['server_name'] or 'Unknown'}",
                value=f"At: {created} | Result: {display}\nPrompt: {desc}",
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft set-state
    # =========================================================================

    @shapecraft_group.command(name="set-state")
    @owner_only()
    @app_commands.describe(
        license_id="License ID to update",
        state="Target license state",
    )
    @app_commands.choices(
        state=[
            app_commands.Choice(name="TRIAL", value="TRIAL"),
            app_commands.Choice(name="ACTIVE", value="ACTIVE"),
            app_commands.Choice(name="GRACE", value="GRACE"),
            app_commands.Choice(name="EXPIRED", value="EXPIRED"),
        ]
    )
    async def set_state(self, interaction: discord.Interaction, license_id: int, state: str):
        """Set a ShapeCraft license to any state (TRIAL/ACTIVE/GRACE/EXPIRED)."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, state, tier, trial_remaining, monthly_used, server_ip, label "
            "FROM shapecraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        if row["state"] == state and (
            (state == "TRIAL" and row["tier"] == "trial")
            or (state == "ACTIVE" and row["tier"] == "standard")
            or state in ("GRACE", "EXPIRED")
        ):
            await interaction.followup.send(
                f"License #{row['id']} ({_display_name(row)}) is already {state}.",
                ephemeral=True,
            )
            return

        before = f"{row['state']} / {row['tier']}"

        if state == "TRIAL":
            await self.db.execute(
                """UPDATE shapecraft_licenses
                   SET state = 'TRIAL', tier = 'trial',
                       trial_remaining = 50,
                       expires_at = NULL, grace_ends_at = NULL,
                       updated_at = NOW()
                   WHERE id = $1""",
                row["id"],
            )
            after = "TRIAL / trial (50 remaining)"
            color = discord.Color.gold()
        elif state == "ACTIVE":
            await self.db.execute(
                """UPDATE shapecraft_licenses
                   SET state = 'ACTIVE', tier = 'standard',
                       monthly_used = 0,
                       monthly_reset_date = date_trunc('month', NOW()) + interval '1 month',
                       expires_at = NULL, grace_ends_at = NULL,
                       updated_at = NOW()
                   WHERE id = $1""",
                row["id"],
            )
            after = "ACTIVE / standard (250/month)"
            color = discord.Color.green()
        elif state == "GRACE":
            await self.db.execute(
                """UPDATE shapecraft_licenses
                   SET state = 'GRACE', updated_at = NOW()
                   WHERE id = $1""",
                row["id"],
            )
            after = f"GRACE / {row['tier']}"
            color = discord.Color.orange()
        else:  # EXPIRED
            await self.db.execute(
                """UPDATE shapecraft_licenses
                   SET state = 'EXPIRED', updated_at = NOW()
                   WHERE id = $1""",
                row["id"],
            )
            after = f"EXPIRED / {row['tier']}"
            color = discord.Color.red()

        embed = discord.Embed(title=f"License \u2192 {state}", color=color)
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        embed.add_field(name="IP", value=row["server_ip"] or "N/A", inline=True)
        embed.add_field(name="Before", value=before, inline=True)
        embed.add_field(name="After", value=after, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft hide
    # =========================================================================

    @shapecraft_group.command(name="hide")
    @owner_only()
    @app_commands.describe(license_id="License ID to hide")
    async def hide(self, interaction: discord.Interaction, license_id: int):
        """Hide a license from default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM shapecraft_licenses WHERE id = $1",
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
            "UPDATE shapecraft_licenses SET hidden = true, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Hidden", color=discord.Color.dark_grey())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft unhide
    # =========================================================================

    @shapecraft_group.command(name="unhide")
    @owner_only()
    @app_commands.describe(license_id="License ID to unhide")
    async def unhide(self, interaction: discord.Interaction, license_id: int):
        """Unhide a license so it appears in default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM shapecraft_licenses WHERE id = $1",
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
            "UPDATE shapecraft_licenses SET hidden = false, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Unhidden", color=discord.Color.green())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /shapecraft label
    # =========================================================================

    @shapecraft_group.command(name="label")
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
            "SELECT id, server_name, label FROM shapecraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        old_display = _display_name(row)

        await self.db.execute(
            "UPDATE shapecraft_licenses SET label = $2, updated_at = NOW() WHERE id = $1",
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
    """Register the ShapeCraft commands cog."""
    await bot.add_cog(ShapeCraftCommands(bot, db_pool))
