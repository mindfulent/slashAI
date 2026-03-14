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
TipSign Slash Commands (Owner Only)

Discord slash commands for querying TipSign data from the theblockacademy backend API.
Restricted to bot owner via OWNER_ID environment variable.
"""

import logging
import math
import os
from datetime import datetime

import aiohttp
import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from commands.views import PaginationView

logger = logging.getLogger("slashAI.commands.tipsign")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
TBA_API_URL = os.getenv(
    "TBA_API_URL",
    os.getenv("RECOGNITION_API_URL", "https://theblock.academy/api").rsplit("/recognition", 1)[0],
)

EMBED_COLOR = 0xD4A574  # Warm wood tone


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


def _format_location(sign: dict) -> str:
    """Format world and coordinates."""
    world = sign.get("world", "unknown")
    # Shorten minecraft:overworld -> overworld
    if world.startswith("minecraft:"):
        world = world[len("minecraft:"):]
    return f"{world} ({sign.get('x', '?')}, {sign.get('y', '?')}, {sign.get('z', '?')})"


def _supporter_indicators(sign: dict) -> str:
    """Return emoji indicators for supporter links."""
    parts = []
    if sign.get("kofi_url"):
        parts.append("Ko-fi")
    if sign.get("patreon_url"):
        parts.append("Patreon")
    return " | ".join(parts) if parts else ""


class TipSignCommands(commands.Cog):
    """
    Slash commands for querying TipSign data (owner-only).

    Commands:
    - /tipsign list - List all tip signs (paginated)
    - /tipsign search <query> - Search by owner or title
    - /tipsign detail <sign_id> - Full details for a sign
    - /tipsign stats - Summary statistics
    """

    tipsign_group = app_commands.Group(
        name="tipsign",
        description="Query TipSign data from the backend (owner only)",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool):
        self.bot = bot
        self.db = db_pool

    async def _fetch_signs(self, params: dict = None) -> dict | None:
        """Fetch signs from the TipSign API. Returns parsed JSON or None on error."""
        url = f"{TBA_API_URL}/tipsigns"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.error("TipSign API returned %d: %s", resp.status, await resp.text())
                        return None
                    return await resp.json()
        except Exception as e:
            logger.error("Failed to fetch TipSign data: %s", e)
            return None

    async def _fetch_sign(self, sign_id: str) -> dict | None:
        """Fetch a single sign by ID."""
        url = f"{TBA_API_URL}/tipsigns/{sign_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json()
        except Exception as e:
            logger.error("Failed to fetch TipSign %s: %s", sign_id, e)
            return None

    def _error_embed(self, message: str = "Could not reach the TipSign API. Is the backend running?") -> discord.Embed:
        return discord.Embed(
            title="TipSign API Error",
            description=message,
            color=discord.Color.red(),
        )

    # =========================================================================
    # /tipsign list
    # =========================================================================

    @tipsign_group.command(name="list")
    @owner_only()
    async def list_signs(self, interaction: discord.Interaction):
        """List all tip signs (paginated)."""
        await interaction.response.defer(ephemeral=True)

        result = await self._fetch_signs()
        if result is None:
            await interaction.followup.send(embed=self._error_embed(), ephemeral=True)
            return

        signs = result.get("data", {}).get("signs", [])
        if not signs:
            await interaction.followup.send(
                embed=discord.Embed(title="TipSigns", description="No tip signs found.", color=EMBED_COLOR),
                ephemeral=True,
            )
            return

        per_page = 10
        total_pages = max(1, math.ceil(len(signs) / per_page))

        def build_page(page: int) -> discord.Embed:
            start = (page - 1) * per_page
            page_signs = signs[start : start + per_page]
            embed = discord.Embed(
                title=f"TipSigns ({len(signs)} total)",
                color=EMBED_COLOR,
                timestamp=datetime.utcnow(),
            )
            lines = []
            for s in page_signs:
                location = _format_location(s)
                supporters = _supporter_indicators(s)
                suffix = f" | {supporters}" if supporters else ""
                lines.append(f"**{s.get('title', 'Untitled')}** by {s.get('owner_username', '?')} \u2022 {location}{suffix}")
            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Page {page}/{total_pages}")
            return embed

        async def fetch_page(page: int) -> discord.Embed:
            return build_page(page)

        embed = build_page(1)
        if total_pages > 1:
            view = PaginationView(
                user_id=interaction.user.id,
                current_page=1,
                total_pages=total_pages,
                fetch_page=fetch_page,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /tipsign search
    # =========================================================================

    @tipsign_group.command(name="search")
    @owner_only()
    @app_commands.describe(query="Search by owner username or sign title")
    async def search(self, interaction: discord.Interaction, query: str):
        """Search tip signs by owner or title."""
        await interaction.response.defer(ephemeral=True)

        result = await self._fetch_signs()
        if result is None:
            await interaction.followup.send(embed=self._error_embed(), ephemeral=True)
            return

        signs = result.get("data", {}).get("signs", [])
        q = query.lower()
        matches = [
            s for s in signs
            if q in (s.get("owner_username") or "").lower()
            or q in (s.get("title") or "").lower()
        ]

        if not matches:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="TipSign Search",
                    description=f"No signs matching `{query}`.",
                    color=EMBED_COLOR,
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"TipSign Search: \"{query}\" ({len(matches)} result{'s' if len(matches) != 1 else ''})",
            color=EMBED_COLOR,
            timestamp=datetime.utcnow(),
        )
        lines = []
        for s in matches[:25]:
            location = _format_location(s)
            supporters = _supporter_indicators(s)
            suffix = f" | {supporters}" if supporters else ""
            lines.append(
                f"**{s.get('title', 'Untitled')}** by {s.get('owner_username', '?')} \u2022 {location}{suffix}\n"
                f"ID: `{s.get('id', '?')}`"
            )
        embed.description = "\n\n".join(lines)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /tipsign detail
    # =========================================================================

    @tipsign_group.command(name="detail")
    @owner_only()
    @app_commands.describe(sign_id="UUID of the tip sign")
    async def detail(self, interaction: discord.Interaction, sign_id: str):
        """Show full details of a specific tip sign."""
        await interaction.response.defer(ephemeral=True)

        result = await self._fetch_sign(sign_id)
        if result is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="TipSign Not Found",
                    description=f"No sign found with ID `{sign_id}`.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        sign = result.get("data", result)
        # Handle if data wraps a single sign vs signs array
        if "signs" in sign:
            sign = sign["signs"][0] if sign["signs"] else None
        if not sign:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="TipSign Not Found",
                    description=f"No sign found with ID `{sign_id}`.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        # Build pages text
        pages = sign.get("pages", [])
        if pages:
            pages_text = "\n\n".join(f"**Page {i+1}:** {p}" for i, p in enumerate(pages))
            if len(pages_text) > 2000:
                pages_text = pages_text[:1997] + "..."
        else:
            pages_text = "*No pages*"

        embed = discord.Embed(
            title=sign.get("title", "Untitled"),
            description=pages_text,
            color=EMBED_COLOR,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Owner", value=sign.get("owner_username", "?"), inline=True)
        embed.add_field(name="Location", value=_format_location(sign), inline=True)
        embed.add_field(name="ID", value=f"`{sign.get('id', '?')}`", inline=False)

        # Supporter URLs
        kofi = sign.get("kofi_url")
        patreon = sign.get("patreon_url")
        if kofi or patreon:
            urls = []
            if kofi:
                urls.append(f"[Ko-fi]({kofi})")
            if patreon:
                urls.append(f"[Patreon]({patreon})")
            embed.add_field(name="Supporter Links", value=" | ".join(urls), inline=False)

        # Timestamps
        timestamps = []
        if sign.get("placed_at"):
            timestamps.append(f"Placed: {sign['placed_at'][:16].replace('T', ' ')}")
        if sign.get("last_edited_at"):
            timestamps.append(f"Edited: {sign['last_edited_at'][:16].replace('T', ' ')}")
        if sign.get("synced_at"):
            timestamps.append(f"Synced: {sign['synced_at'][:16].replace('T', ' ')}")
        if timestamps:
            embed.add_field(name="Timestamps", value="\n".join(timestamps), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /tipsign stats
    # =========================================================================

    @tipsign_group.command(name="stats")
    @owner_only()
    async def stats(self, interaction: discord.Interaction):
        """Summary statistics for all tip signs."""
        await interaction.response.defer(ephemeral=True)

        result = await self._fetch_signs()
        if result is None:
            await interaction.followup.send(embed=self._error_embed(), ephemeral=True)
            return

        signs = result.get("data", {}).get("signs", [])

        total = len(signs)
        owners = set(s.get("owner_username") for s in signs if s.get("owner_username"))
        with_kofi = sum(1 for s in signs if s.get("kofi_url"))
        with_patreon = sum(1 for s in signs if s.get("patreon_url"))

        # Most prolific author
        author_counts: dict[str, int] = {}
        for s in signs:
            name = s.get("owner_username")
            if name:
                author_counts[name] = author_counts.get(name, 0) + 1
        top_author = max(author_counts, key=author_counts.get) if author_counts else "N/A"
        top_count = author_counts.get(top_author, 0)

        embed = discord.Embed(
            title="TipSign Statistics",
            color=EMBED_COLOR,
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Total Signs", value=str(total), inline=True)
        embed.add_field(name="Unique Owners", value=str(len(owners)), inline=True)
        embed.add_field(name="With Ko-fi", value=str(with_kofi), inline=True)
        embed.add_field(name="With Patreon", value=str(with_patreon), inline=True)
        embed.add_field(
            name="Most Prolific",
            value=f"{top_author} ({top_count} sign{'s' if top_count != 1 else ''})" if top_author != "N/A" else "N/A",
            inline=True,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot, db_pool: asyncpg.Pool):
    """Register the TipSign commands cog."""
    await bot.add_cog(TipSignCommands(bot, db_pool))
