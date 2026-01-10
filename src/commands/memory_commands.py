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
Memory Management Slash Commands

Discord slash commands for users to view, search, and manage their memories.
"""

import logging
from datetime import datetime
from typing import Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from analytics import track
from .views import PaginationView, DeleteConfirmView, MemoryDetailView

logger = logging.getLogger("slashAI.commands.memory")

# Page size for memory lists
PAGE_SIZE = 10


class MemoryCommands(commands.Cog):
    """
    Slash commands for memory management.

    Commands:
    - /memories list - List your memories
    - /memories search - Search your memories
    - /memories mentions - View others' public memories about you
    - /memories view - View full memory details
    - /memories delete - Delete a memory
    - /memories stats - View your memory statistics
    """

    memories_group = app_commands.Group(
        name="memories",
        description="View and manage your slashAI memories",
    )

    def __init__(self, bot: commands.Bot, db_pool: asyncpg.Pool, memory_manager):
        self.bot = bot
        self.db = db_pool
        self.memory = memory_manager

    # =========================================================================
    # /memories list
    # =========================================================================

    @memories_group.command(name="list")
    @app_commands.describe(
        page="Page number (default: 1)",
        privacy="Filter by privacy level (default: all)",
    )
    @app_commands.choices(
        privacy=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="DM", value="dm"),
            app_commands.Choice(name="Channel Restricted", value="channel_restricted"),
            app_commands.Choice(name="Guild Public", value="guild_public"),
            app_commands.Choice(name="Global", value="global"),
        ]
    )
    async def list_memories(
        self,
        interaction: discord.Interaction,
        page: int = 1,
        privacy: str = "all",
    ):
        """List your memories."""
        await interaction.response.defer(ephemeral=True)

        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "memories", "subcommand": "list", "page": page, "privacy": privacy},
        )

        user_id = interaction.user.id
        offset = (page - 1) * PAGE_SIZE

        # Fetch memories
        privacy_filter = None if privacy == "all" else privacy
        memories, total = await self.memory.list_user_memories(
            user_id, privacy_filter=privacy_filter, limit=PAGE_SIZE, offset=offset
        )

        if total == 0:
            await interaction.followup.send(
                "You don't have any memories stored yet. Chat with me to build your memory!",
                ephemeral=True,
            )
            return

        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if page > total_pages:
            await interaction.followup.send(
                f"Page {page} doesn't exist. You have {total_pages} page(s).",
                ephemeral=True,
            )
            return

        # Create embed
        embed = self._format_memory_list(memories, page, total_pages, total, privacy)

        # Create pagination view if needed
        if total_pages > 1:

            async def fetch_page(new_page: int) -> discord.Embed:
                new_offset = (new_page - 1) * PAGE_SIZE
                new_memories, _ = await self.memory.list_user_memories(
                    user_id, privacy_filter=privacy_filter, limit=PAGE_SIZE, offset=new_offset
                )
                return self._format_memory_list(new_memories, new_page, total_pages, total, privacy)

            view = PaginationView(user_id, page, total_pages, fetch_page)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    def _format_memory_list(
        self,
        memories: list[dict],
        page: int,
        total_pages: int,
        total: int,
        privacy_filter: str,
    ) -> discord.Embed:
        """Format a list of memories as an embed."""
        filter_text = f" ({privacy_filter})" if privacy_filter != "all" else ""
        embed = discord.Embed(
            title=f"Your Memories{filter_text}",
            description=f"Page {page}/{total_pages} • {total} total memories",
            color=discord.Color.blue(),
        )

        for mem in memories:
            # Truncate summary if too long
            summary = mem["topic_summary"]
            if len(summary) > 100:
                summary = summary[:97] + "..."

            # Format updated time
            updated = mem.get("updated_at")
            updated_str = updated.strftime("%Y-%m-%d") if updated else "Unknown"

            # Privacy icon
            privacy_icons = {
                "dm": "🔒",
                "channel_restricted": "🔐",
                "guild_public": "📢",
                "global": "🌐",
            }
            privacy_icon = privacy_icons.get(mem["privacy_level"], "❓")

            embed.add_field(
                name=f"[{mem['id']}] {privacy_icon} {mem['memory_type']}",
                value=f"{summary}\n*Updated: {updated_str}*",
                inline=False,
            )

        embed.set_footer(text="Use /memories view <id> to see full details")
        return embed

    # =========================================================================
    # /memories search
    # =========================================================================

    @memories_group.command(name="search")
    @app_commands.describe(
        query="Search term to find in your memories",
        page="Page number (default: 1)",
    )
    async def search_memories(
        self,
        interaction: discord.Interaction,
        query: str,
        page: int = 1,
    ):
        """Search your memories by text."""
        await interaction.response.defer(ephemeral=True)

        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "memories", "subcommand": "search", "page": page},
        )

        user_id = interaction.user.id
        offset = (page - 1) * PAGE_SIZE

        # Fetch memories
        memories, total = await self.memory.search_user_memories(
            user_id, query, limit=PAGE_SIZE, offset=offset
        )

        if total == 0:
            await interaction.followup.send(
                f'No memories found matching "{query}".',
                ephemeral=True,
            )
            return

        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if page > total_pages:
            await interaction.followup.send(
                f"Page {page} doesn't exist. You have {total_pages} page(s).",
                ephemeral=True,
            )
            return

        # Create embed
        embed = self._format_search_results(memories, query, page, total_pages, total)

        # Create pagination view if needed
        if total_pages > 1:

            async def fetch_page(new_page: int) -> discord.Embed:
                new_offset = (new_page - 1) * PAGE_SIZE
                new_memories, _ = await self.memory.search_user_memories(
                    user_id, query, limit=PAGE_SIZE, offset=new_offset
                )
                return self._format_search_results(new_memories, query, new_page, total_pages, total)

            view = PaginationView(user_id, page, total_pages, fetch_page)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    def _format_search_results(
        self,
        memories: list[dict],
        query: str,
        page: int,
        total_pages: int,
        total: int,
    ) -> discord.Embed:
        """Format search results as an embed."""
        embed = discord.Embed(
            title=f'Search Results: "{query}"',
            description=f"Page {page}/{total_pages} • {total} matches",
            color=discord.Color.green(),
        )

        for mem in memories:
            summary = mem["topic_summary"]
            if len(summary) > 100:
                summary = summary[:97] + "..."

            updated = mem.get("updated_at")
            updated_str = updated.strftime("%Y-%m-%d") if updated else "Unknown"

            embed.add_field(
                name=f"[{mem['id']}] {mem['memory_type']} | {mem['privacy_level']}",
                value=f"{summary}\n*Updated: {updated_str}*",
                inline=False,
            )

        embed.set_footer(text="Use /memories view <id> to see full details")
        return embed

    # =========================================================================
    # /memories mentions
    # =========================================================================

    @memories_group.command(name="mentions")
    @app_commands.describe(page="Page number (default: 1)")
    async def view_mentions(
        self,
        interaction: discord.Interaction,
        page: int = 1,
    ):
        """View public memories from others that mention you."""
        await interaction.response.defer(ephemeral=True)

        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "memories", "subcommand": "mentions", "page": page},
        )

        # This command only works in guilds
        if not interaction.guild:
            await interaction.followup.send(
                "This command only works in servers, not in DMs.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        guild_id = interaction.guild.id
        offset = (page - 1) * PAGE_SIZE

        # Build list of identifiers to search for
        identifiers = await self._get_user_identifiers(user_id, interaction.user)

        if not identifiers:
            await interaction.followup.send(
                "Could not determine your identifiers for searching.",
                ephemeral=True,
            )
            return

        # Fetch mentions
        memories, total = await self.memory.find_mentions(
            user_id, guild_id, identifiers, limit=PAGE_SIZE, offset=offset
        )

        if total == 0:
            await interaction.followup.send(
                "No public memories from others mention you yet.",
                ephemeral=True,
            )
            return

        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        if page > total_pages:
            await interaction.followup.send(
                f"Page {page} doesn't exist. You have {total_pages} page(s).",
                ephemeral=True,
            )
            return

        # Create embed
        embed = await self._format_mentions(memories, page, total_pages, total, interaction.guild)

        # Create pagination view if needed
        if total_pages > 1:

            async def fetch_page(new_page: int) -> discord.Embed:
                new_offset = (new_page - 1) * PAGE_SIZE
                new_memories, _ = await self.memory.find_mentions(
                    user_id, guild_id, identifiers, limit=PAGE_SIZE, offset=new_offset
                )
                return await self._format_mentions(new_memories, new_page, total_pages, total, interaction.guild)

            view = PaginationView(user_id, page, total_pages, fetch_page)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def _get_user_identifiers(
        self, user_id: int, user: discord.User
    ) -> list[str]:
        """
        Get identifiers to search for when finding mentions.

        Includes: username, display name, and IGN (if known).
        """
        identifiers = []

        # Add Discord username and display name
        if user.name:
            identifiers.append(user.name)
        if user.display_name and user.display_name != user.name:
            identifiers.append(user.display_name)

        # Look up IGN from user's global memories
        try:
            row = await self.db.fetchrow(
                """
                SELECT topic_summary FROM memories
                WHERE user_id = $1 AND privacy_level = 'global'
                  AND topic_summary ILIKE 'IGN:%'
                LIMIT 1
                """,
                user_id,
            )
            if row:
                # Extract IGN from "IGN: value" format
                ign = row["topic_summary"].split(":", 1)[1].strip()
                if ign and ign not in identifiers:
                    identifiers.append(ign)
        except Exception as e:
            logger.warning(f"Failed to look up IGN for user={user_id}: {e}")

        return identifiers

    async def _format_mentions(
        self,
        memories: list[dict],
        page: int,
        total_pages: int,
        total: int,
        guild: discord.Guild,
    ) -> discord.Embed:
        """Format mentions as an embed."""
        embed = discord.Embed(
            title="Mentions of You",
            description=f"Public memories from others that mention you\nPage {page}/{total_pages} • {total} total",
            color=discord.Color.purple(),
        )

        for mem in memories:
            summary = mem["topic_summary"]
            if len(summary) > 100:
                summary = summary[:97] + "..."

            # Resolve owner name
            owner_id = mem["user_id"]
            member = guild.get_member(owner_id)
            owner_name = member.display_name if member else f"User {owner_id}"

            updated = mem.get("updated_at")
            updated_str = updated.strftime("%Y-%m-%d") if updated else "Unknown"

            embed.add_field(
                name=f"[{mem['id']}] From: {owner_name}",
                value=f"{summary}\n*Updated: {updated_str}*",
                inline=False,
            )

        embed.set_footer(text="These memories are read-only (you cannot delete them)")
        return embed

    # =========================================================================
    # /memories view
    # =========================================================================

    @memories_group.command(name="view")
    @app_commands.describe(memory_id="Memory ID to view")
    async def view_memory(
        self,
        interaction: discord.Interaction,
        memory_id: int,
    ):
        """View full details of a memory."""
        await interaction.response.defer(ephemeral=True)

        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "memories", "subcommand": "view", "memory_id": memory_id},
        )

        user_id = interaction.user.id
        memory = await self.memory.get_memory(memory_id)

        if not memory:
            await interaction.followup.send(
                f"Memory #{memory_id} not found.",
                ephemeral=True,
            )
            return

        # Check access permissions
        can_view = False
        can_delete = False

        if memory["user_id"] == user_id:
            # Own memory - full access
            can_view = True
            can_delete = True
        elif memory["privacy_level"] == "guild_public":
            # Others' guild_public memory - check if same guild
            if interaction.guild and memory.get("origin_guild_id") == interaction.guild.id:
                can_view = True
                can_delete = False

        if not can_view:
            await interaction.followup.send(
                f"You don't have permission to view memory #{memory_id}.",
                ephemeral=True,
            )
            return

        # Format the memory
        embed = await self._format_memory_detail(memory, interaction.guild, can_delete)

        # Add delete button if allowed
        if can_delete:

            async def on_delete(inter: discord.Interaction, mem_id: int):
                await self._confirm_delete(inter, mem_id)

            view = MemoryDetailView(user_id, memory_id, can_delete=True, on_delete=on_delete)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def _format_memory_detail(
        self,
        memory: dict,
        guild: Optional[discord.Guild],
        is_owner: bool,
    ) -> discord.Embed:
        """Format a single memory with full details."""
        privacy_icons = {
            "dm": "🔒 DM (Private)",
            "channel_restricted": "🔐 Channel Restricted",
            "guild_public": "📢 Guild Public",
            "global": "🌐 Global",
        }

        embed = discord.Embed(
            title=f"Memory #{memory['id']}",
            color=discord.Color.blue() if is_owner else discord.Color.purple(),
        )

        # Summary
        embed.add_field(
            name="Summary",
            value=memory["topic_summary"],
            inline=False,
        )

        # Raw dialogue (truncate if too long)
        raw = memory.get("raw_dialogue", "")
        if len(raw) > 500:
            raw = raw[:497] + "..."
        if raw:
            embed.add_field(
                name="Source Dialogue",
                value=f"```{raw}```",
                inline=False,
            )

        # Metadata
        privacy_str = privacy_icons.get(memory["privacy_level"], memory["privacy_level"])
        confidence = memory.get("confidence", 0)
        source_count = memory.get("source_count", 1)

        embed.add_field(name="Type", value=memory["memory_type"], inline=True)
        embed.add_field(name="Privacy", value=privacy_str, inline=True)
        embed.add_field(name="Confidence", value=f"{confidence:.0%}", inline=True)
        embed.add_field(name="Sources", value=str(source_count), inline=True)

        # Timestamps
        created = memory.get("created_at")
        updated = memory.get("updated_at")
        accessed = memory.get("last_accessed_at")

        timestamps = []
        if created:
            timestamps.append(f"Created: {created.strftime('%Y-%m-%d %H:%M')}")
        if updated:
            timestamps.append(f"Updated: {updated.strftime('%Y-%m-%d %H:%M')}")
        if accessed:
            timestamps.append(f"Last accessed: {accessed.strftime('%Y-%m-%d %H:%M')}")

        if timestamps:
            embed.add_field(
                name="Timestamps",
                value="\n".join(timestamps),
                inline=False,
            )

        # Owner info if viewing someone else's memory
        if not is_owner and guild:
            owner_id = memory["user_id"]
            member = guild.get_member(owner_id)
            owner_name = member.display_name if member else f"User {owner_id}"
            embed.set_footer(text=f"Memory belongs to: {owner_name}")
        elif is_owner:
            embed.set_footer(text="This is your memory. Click Delete to remove it.")

        return embed

    async def _confirm_delete(self, interaction: discord.Interaction, memory_id: int):
        """Show delete confirmation dialog."""
        memory = await self.memory.get_memory(memory_id)

        if not memory or memory["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                "Memory not found or you don't own it.",
                ephemeral=True,
            )
            return

        # Show confirmation
        embed = discord.Embed(
            title="Confirm Deletion",
            description=f"Are you sure you want to delete this memory?\n\n**{memory['topic_summary']}**",
            color=discord.Color.red(),
        )

        async def on_confirm(inter: discord.Interaction, mem_id: int):
            success = await self.memory.delete_memory(mem_id, inter.user.id)
            if success:
                await inter.response.edit_message(
                    content=f"Memory #{mem_id} has been deleted.",
                    embed=None,
                    view=None,
                )
            else:
                await inter.response.edit_message(
                    content="Failed to delete memory. It may have already been deleted.",
                    embed=None,
                    view=None,
                )

        view = DeleteConfirmView(interaction.user.id, memory_id, on_confirm)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # =========================================================================
    # /memories delete
    # =========================================================================

    @memories_group.command(name="delete")
    @app_commands.describe(memory_id="Memory ID to delete")
    async def delete_memory(
        self,
        interaction: discord.Interaction,
        memory_id: int,
    ):
        """Delete one of your memories."""
        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "memories", "subcommand": "delete", "memory_id": memory_id},
        )
        await self._confirm_delete(interaction, memory_id)

    # =========================================================================
    # /memories stats
    # =========================================================================

    @memories_group.command(name="stats")
    async def memory_stats(self, interaction: discord.Interaction):
        """View your memory statistics."""
        await interaction.response.defer(ephemeral=True)

        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "memories", "subcommand": "stats"},
        )

        user_id = interaction.user.id
        stats = await self.memory.get_user_stats(user_id)

        if stats["total"] == 0:
            await interaction.followup.send(
                "You don't have any memories stored yet. Chat with me to build your memory!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Memory Statistics",
            color=discord.Color.gold(),
        )

        embed.add_field(
            name="Total Memories",
            value=str(stats["total"]),
            inline=True,
        )

        # Privacy breakdown
        privacy_icons = {"dm": "🔒", "channel_restricted": "🔐", "guild_public": "📢", "global": "🌐"}
        privacy_lines = []
        for level, count in stats["by_privacy"].items():
            icon = privacy_icons.get(level, "❓")
            privacy_lines.append(f"{icon} {level}: {count}")

        if privacy_lines:
            embed.add_field(
                name="By Privacy Level",
                value="\n".join(privacy_lines),
                inline=True,
            )

        # Type breakdown
        type_lines = []
        for mem_type, count in stats["by_type"].items():
            type_lines.append(f"{mem_type}: {count}")

        if type_lines:
            embed.add_field(
                name="By Type",
                value="\n".join(type_lines),
                inline=True,
            )

        # Last updated
        if stats["last_updated"]:
            embed.add_field(
                name="Last Updated",
                value=stats["last_updated"].strftime("%Y-%m-%d %H:%M"),
                inline=False,
            )

        embed.set_footer(text="Use /memories list to browse your memories")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    """
    Standard discord.py cog setup function.

    Note: This cog requires a memory_manager to be passed, so it's loaded
    manually in discord_bot.py rather than using this function.
    """
    pass
