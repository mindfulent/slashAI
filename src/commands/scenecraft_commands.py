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
SceneCraft Slash Commands (Owner Only)

Discord slash commands for viewing SceneCraft license and server data.
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

logger = logging.getLogger("slashAI.commands.scenecraft")

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


class SceneCraftCommands(commands.Cog):
    """
    Slash commands for viewing SceneCraft data (owner-only).

    Commands:
    - /scenecraft licenses - List all licenses
    - /scenecraft servers - Per-server license details
    - /scenecraft exports - Recent export events (telemetry)
    - /scenecraft activate <server_ip> - Activate a license (ACTIVE/unlimited)
    - /scenecraft deactivate <server_ip> - Deactivate a license (EXPIRED)
    - /scenecraft hide <license_id> - Hide a license from default listings
    - /scenecraft unhide <license_id> - Unhide a license
    - /scenecraft label <license_id> [name] - Set or clear a display label
    """

    scenecraft_group = app_commands.Group(
        name="scenecraft",
        description="View SceneCraft license and server data (owner only)",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool

    # =========================================================================
    # /scenecraft licenses
    # =========================================================================

    @scenecraft_group.command(name="licenses")
    @owner_only()
    @app_commands.describe(show_hidden="Include hidden licenses (default: False)")
    async def licenses(self, interaction: discord.Interaction, show_hidden: bool = False):
        """List all SceneCraft licenses."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT id, server_name, license_key, state, tier, exports_remaining,
                   last_validated, server_ip, hidden, label
            FROM scenecraft_licenses
            WHERE ($1 OR hidden = false)
            ORDER BY id ASC
            """,
            show_hidden,
        )

        if not rows:
            await interaction.followup.send("No SceneCraft licenses found.", ephemeral=True)
            return

        title = f"SceneCraft Licenses ({len(rows)})"
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
            exports = str(row["exports_remaining"]) if row["exports_remaining"] is not None else "N/A"
            validated = row["last_validated"].strftime("%Y-%m-%d %H:%M") if row["last_validated"] else "Never"
            ip = row["server_ip"] or "N/A"
            geo = geo_map.get(row["server_ip"], "")
            location = f" ({geo})" if geo else ""
            hidden_marker = " [HIDDEN]" if row["hidden"] else ""

            embed.add_field(
                name=f"#{row['id']} \u2014 {_display_name(row)} ({row['state']}){hidden_marker}",
                value=(
                    f"Key: `{key_preview}` | Tier: {row['tier'] or 'N/A'}\n"
                    f"IP: {ip}{location} | Sessions: {exports} | Validated: {validated}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft servers
    # =========================================================================

    @scenecraft_group.command(name="servers")
    @owner_only()
    @app_commands.describe(
        server_id="Optional license ID to view details for a specific server",
        show_hidden="Include hidden servers (default: False)",
    )
    async def servers(self, interaction: discord.Interaction, server_id: int = None, show_hidden: bool = False):
        """Per-server SceneCraft license details."""
        await interaction.response.defer(ephemeral=True)

        if server_id:
            # Explicit lookup bypasses hidden filter
            rows = await self.db.fetch(
                """
                SELECT id, server_id as sid, server_name, state, tier,
                       server_ip, exports_remaining, last_validated,
                       hidden, label
                FROM scenecraft_licenses
                WHERE id = $1
                ORDER BY id ASC
                """,
                server_id,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT id, server_id as sid, server_name, state, tier,
                       server_ip, exports_remaining, last_validated,
                       hidden, label
                FROM scenecraft_licenses
                WHERE ($1 OR hidden = false)
                ORDER BY id ASC
                """,
                show_hidden,
            )

        if not rows:
            msg = f"No server found with ID `{server_id}`." if server_id else "No SceneCraft servers found."
            await interaction.followup.send(msg, ephemeral=True)
            return

        title = f"SceneCraft Servers ({len(rows)})"
        if show_hidden and not server_id:
            title += " (incl. hidden)"

        geo_map = await resolve_geo([r["server_ip"] for r in rows if r["server_ip"]])

        embed = discord.Embed(
            title=title,
            color=discord.Color.purple(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            exports = str(row["exports_remaining"]) if row["exports_remaining"] is not None else "N/A"
            validated = row["last_validated"].strftime("%Y-%m-%d %H:%M") if row["last_validated"] else "Never"
            ip = row["server_ip"] or "N/A"
            geo = geo_map.get(row["server_ip"], "")
            location = f" ({geo})" if geo else ""
            sid_preview = row["sid"][:12] + "..." if row["sid"] and len(row["sid"]) > 12 else (row["sid"] or "N/A")
            hidden_marker = " [HIDDEN]" if row["hidden"] else ""

            embed.add_field(
                name=f"#{row['id']} \u2014 {_display_name(row)} ({row['state']}){hidden_marker}",
                value=(
                    f"Server ID: `{sid_preview}`\n"
                    f"IP: {ip}{location} | Tier: {row['tier'] or 'N/A'}\n"
                    f"Sessions: {exports} | Validated: {validated}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


    # =========================================================================
    # /scenecraft exports
    # =========================================================================

    @scenecraft_group.command(name="exports")
    @owner_only()
    @app_commands.describe(
        limit="Number of recent events to show (default 10, max 25)",
        player="Filter by player name",
    )
    async def exports(
        self,
        interaction: discord.Interaction,
        limit: int = 10,
        player: str = None,
    ):
        """View recent SceneCraft export events (telemetry)."""
        await interaction.response.defer(ephemeral=True)

        limit = min(max(1, limit), 25)

        if player:
            rows = await self.db.fetch(
                """
                SELECT id, event_type, player_name, player_uuid, server_address,
                       mod_version, highlight_count, total_frames,
                       render_width, render_height, render_fps,
                       error_message, created_at
                FROM scenecraft_export_events
                WHERE LOWER(player_name) LIKE LOWER($1)
                ORDER BY id ASC
                LIMIT $2
                """,
                f"%{player}%",
                limit,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT id, event_type, player_name, player_uuid, server_address,
                       mod_version, highlight_count, total_frames,
                       render_width, render_height, render_fps,
                       error_message, created_at
                FROM scenecraft_export_events
                ORDER BY id ASC
                LIMIT $1
                """,
                limit,
            )

        if not rows:
            msg = "No export events found."
            if player:
                msg = f"No export events found for player matching '{player}'."
            await interaction.followup.send(msg, ephemeral=True)
            return

        # Summary stats
        total_count = await self.db.fetchval(
            "SELECT COUNT(*) FROM scenecraft_export_events"
        )
        success_count = await self.db.fetchval(
            "SELECT COUNT(*) FROM scenecraft_export_events WHERE event_type = 'export_complete'"
        )
        fail_count = await self.db.fetchval(
            "SELECT COUNT(*) FROM scenecraft_export_events WHERE event_type = 'export_failed'"
        )

        embed = discord.Embed(
            title="SceneCraft Export Events",
            description=f"**Total:** {total_count} events ({success_count} success, {fail_count} failed)",
            color=discord.Color.green() if fail_count == 0 else discord.Color.orange(),
            timestamp=datetime.utcnow(),
        )

        for row in rows:
            is_success = row["event_type"] == "export_complete"
            icon = "\u2705" if is_success else "\u274c"
            name = row["player_name"] or row["player_uuid"][:8]
            ts = row["created_at"].strftime("%Y-%m-%d %H:%M") if row["created_at"] else "?"

            if is_success:
                highlights = row["highlight_count"] or "?"
                frames = row["total_frames"] or "?"
                res = (
                    f"{row['render_width']}x{row['render_height']}@{row['render_fps']}fps"
                    if row["render_width"]
                    else "N/A"
                )
                value = (
                    f"Server: {row['server_address']}\n"
                    f"Highlights: {highlights} | Frames: {frames} | {res}\n"
                    f"v{row['mod_version'] or '?'} | {ts}"
                )
            else:
                error = row["error_message"] or "Unknown error"
                if len(error) > 100:
                    error = error[:97] + "..."
                value = (
                    f"Server: {row['server_address']}\n"
                    f"Error: {error}\n"
                    f"v{row['mod_version'] or '?'} | {ts}"
                )

            embed.add_field(
                name=f"{icon} #{row['id']} \u2014 {name}",
                value=value,
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft activate
    # =========================================================================

    @scenecraft_group.command(name="activate")
    @owner_only()
    @app_commands.describe(server_ip="Server IP address to activate")
    async def activate(self, interaction: discord.Interaction, server_ip: str):
        """Activate a SceneCraft license (set to ACTIVE/unlimited) by server IP."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            "SELECT id, server_name, state, tier, exports_remaining, server_ip "
            "FROM scenecraft_licenses WHERE server_ip = $1",
            server_ip,
        )

        if not rows:
            await interaction.followup.send(
                f"No license found for IP `{server_ip}`.", ephemeral=True
            )
            return

        if len(rows) > 1:
            lines = [f"Multiple licenses found for `{server_ip}`:"]
            for r in rows:
                lines.append(
                    f"  #{r['id']} \u2014 {r['server_name'] or 'Unknown'} ({r['state']})"
                )
            lines.append("Use `/scenecraft activate_by_id <id>` to target one.")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        row = rows[0]
        if row["state"] == "ACTIVE" and row["tier"] == "standard":
            await interaction.followup.send(
                f"License #{row['id']} ({row['server_name']}) is already ACTIVE.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            """UPDATE scenecraft_licenses
               SET state = 'ACTIVE', tier = 'standard',
                   exports_remaining = NULL, expires_at = NULL,
                   updated_at = NOW()
               WHERE id = $1""",
            row["id"],
        )

        embed = discord.Embed(title="License Activated", color=discord.Color.green())
        embed.add_field(name="Server", value=row["server_name"] or "Unknown", inline=True)
        embed.add_field(name="IP", value=server_ip, inline=True)
        embed.add_field(
            name="Before", value=f"{row['state']} / {row['tier']}", inline=True
        )
        embed.add_field(name="After", value="ACTIVE / standard (unlimited)", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft deactivate
    # =========================================================================

    @scenecraft_group.command(name="deactivate")
    @owner_only()
    @app_commands.describe(server_ip="Server IP address to deactivate")
    async def deactivate(self, interaction: discord.Interaction, server_ip: str):
        """Deactivate a SceneCraft license (set to EXPIRED) by server IP."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            "SELECT id, server_name, state, tier, server_ip "
            "FROM scenecraft_licenses WHERE server_ip = $1",
            server_ip,
        )

        if not rows:
            await interaction.followup.send(
                f"No license found for IP `{server_ip}`.", ephemeral=True
            )
            return

        if len(rows) > 1:
            lines = [f"Multiple licenses found for `{server_ip}`:"]
            for r in rows:
                lines.append(
                    f"  #{r['id']} \u2014 {r['server_name'] or 'Unknown'} ({r['state']})"
                )
            lines.append("Use `/scenecraft deactivate_by_id <id>` to target one.")
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        row = rows[0]
        if row["state"] == "EXPIRED":
            await interaction.followup.send(
                f"License #{row['id']} ({row['server_name']}) is already EXPIRED.",
                ephemeral=True,
            )
            return

        await self.db.execute(
            """UPDATE scenecraft_licenses
               SET state = 'EXPIRED', updated_at = NOW()
               WHERE id = $1""",
            row["id"],
        )

        embed = discord.Embed(title="License Deactivated", color=discord.Color.red())
        embed.add_field(name="Server", value=row["server_name"] or "Unknown", inline=True)
        embed.add_field(name="IP", value=server_ip, inline=True)
        embed.add_field(
            name="Before", value=f"{row['state']} / {row['tier']}", inline=True
        )
        embed.add_field(name="After", value="EXPIRED", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft activate_by_id
    # =========================================================================

    @scenecraft_group.command(name="activate_by_id")
    @owner_only()
    @app_commands.describe(license_id="License ID to activate")
    async def activate_by_id(self, interaction: discord.Interaction, license_id: int):
        """Activate a specific SceneCraft license by ID."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, state, tier, exports_remaining, server_ip "
            "FROM scenecraft_licenses WHERE id = $1",
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
            """UPDATE scenecraft_licenses
               SET state = 'ACTIVE', tier = 'standard',
                   exports_remaining = NULL, expires_at = NULL,
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
    # /scenecraft deactivate_by_id
    # =========================================================================

    @scenecraft_group.command(name="deactivate_by_id")
    @owner_only()
    @app_commands.describe(license_id="License ID to deactivate")
    async def deactivate_by_id(self, interaction: discord.Interaction, license_id: int):
        """Deactivate a specific SceneCraft license by ID."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, state, tier, server_ip "
            "FROM scenecraft_licenses WHERE id = $1",
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
            """UPDATE scenecraft_licenses
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
    # /scenecraft hide
    # =========================================================================

    @scenecraft_group.command(name="hide")
    @owner_only()
    @app_commands.describe(license_id="License ID to hide")
    async def hide(self, interaction: discord.Interaction, license_id: int):
        """Hide a license from default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM scenecraft_licenses WHERE id = $1",
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
            "UPDATE scenecraft_licenses SET hidden = true, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Hidden", color=discord.Color.dark_grey())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft unhide
    # =========================================================================

    @scenecraft_group.command(name="unhide")
    @owner_only()
    @app_commands.describe(license_id="License ID to unhide")
    async def unhide(self, interaction: discord.Interaction, license_id: int):
        """Unhide a license so it appears in default listings."""
        await interaction.response.defer(ephemeral=True)

        row = await self.db.fetchrow(
            "SELECT id, server_name, hidden, label FROM scenecraft_licenses WHERE id = $1",
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
            "UPDATE scenecraft_licenses SET hidden = false, updated_at = NOW() WHERE id = $1",
            license_id,
        )

        embed = discord.Embed(title="License Unhidden", color=discord.Color.green())
        embed.add_field(name="License", value=f"#{row['id']}", inline=True)
        embed.add_field(name="Server", value=_display_name(row), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft label
    # =========================================================================

    @scenecraft_group.command(name="label")
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
            "SELECT id, server_name, label FROM scenecraft_licenses WHERE id = $1",
            license_id,
        )

        if not row:
            await interaction.followup.send(
                f"No license found with ID `{license_id}`.", ephemeral=True
            )
            return

        old_display = _display_name(row)

        await self.db.execute(
            "UPDATE scenecraft_licenses SET label = $2, updated_at = NOW() WHERE id = $1",
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
    """Register the SceneCraft commands cog."""
    await bot.add_cog(SceneCraftCommands(bot, db_pool))
