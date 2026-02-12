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


class SceneCraftCommands(commands.Cog):
    """
    Slash commands for viewing SceneCraft data (owner-only).

    Commands:
    - /scenecraft licenses - List all licenses
    - /scenecraft servers - Per-server license details
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
    async def licenses(self, interaction: discord.Interaction):
        """List all SceneCraft licenses."""
        await interaction.response.defer(ephemeral=True)

        rows = await self.db.fetch(
            """
            SELECT server_name, license_key, state, tier, exports_remaining,
                   last_validated, expires_at, server_ip
            FROM scenecraft_licenses
            ORDER BY created_at DESC
            """
        )

        if not rows:
            await interaction.followup.send("No SceneCraft licenses found.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"SceneCraft Licenses ({len(rows)})",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:  # Discord embed field limit
            key_preview = row["license_key"][:8] + "..." if row["license_key"] else "N/A"
            exports = str(row["exports_remaining"]) if row["exports_remaining"] is not None else "N/A"
            validated = row["last_validated"].strftime("%Y-%m-%d %H:%M") if row["last_validated"] else "Never"
            expires = row["expires_at"].strftime("%Y-%m-%d") if row["expires_at"] else "N/A"
            ip = row["server_ip"] or "N/A"

            embed.add_field(
                name=f"{row['server_name'] or 'Unknown'} ({row['state']})",
                value=(
                    f"Key: `{key_preview}` | Tier: {row['tier'] or 'N/A'}\n"
                    f"IP: {ip} | Sessions: {exports} | Validated: {validated}\n"
                    f"Expires: {expires}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /scenecraft servers
    # =========================================================================

    @scenecraft_group.command(name="servers")
    @owner_only()
    @app_commands.describe(server_id="Optional license ID to view details for a specific server")
    async def servers(self, interaction: discord.Interaction, server_id: int = None):
        """Per-server SceneCraft license details."""
        await interaction.response.defer(ephemeral=True)

        if server_id:
            rows = await self.db.fetch(
                """
                SELECT id, server_id as sid, server_name, state, tier,
                       server_ip, exports_remaining, last_validated, expires_at
                FROM scenecraft_licenses
                WHERE id = $1
                ORDER BY created_at DESC
                """,
                server_id,
            )
        else:
            rows = await self.db.fetch(
                """
                SELECT id, server_id as sid, server_name, state, tier,
                       server_ip, exports_remaining, last_validated, expires_at
                FROM scenecraft_licenses
                ORDER BY created_at DESC
                """
            )

        if not rows:
            msg = f"No server found with ID `{server_id}`." if server_id else "No SceneCraft servers found."
            await interaction.followup.send(msg, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"SceneCraft Servers ({len(rows)})",
            color=discord.Color.purple(),
            timestamp=datetime.utcnow(),
        )

        for row in rows[:25]:
            exports = str(row["exports_remaining"]) if row["exports_remaining"] is not None else "N/A"
            validated = row["last_validated"].strftime("%Y-%m-%d %H:%M") if row["last_validated"] else "Never"
            expires = row["expires_at"].strftime("%Y-%m-%d") if row["expires_at"] else "N/A"
            ip = row["server_ip"] or "N/A"
            sid_preview = row["sid"][:12] + "..." if row["sid"] and len(row["sid"]) > 12 else (row["sid"] or "N/A")

            embed.add_field(
                name=f"#{row['id']} — {row['server_name'] or 'Unknown'} ({row['state']})",
                value=(
                    f"Server ID: `{sid_preview}`\n"
                    f"IP: {ip} | Tier: {row['tier'] or 'N/A'}\n"
                    f"Sessions: {exports} | Validated: {validated}\n"
                    f"Expires: {expires}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, db_pool: asyncpg.Pool):
    """Register the SceneCraft commands cog."""
    await bot.add_cog(SceneCraftCommands(bot, db_pool))
