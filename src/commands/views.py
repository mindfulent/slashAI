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
Discord UI Components for Memory Commands

Provides interactive views for pagination and confirmation dialogs.
"""

import discord
from typing import Callable, Awaitable, Optional


class PaginationView(discord.ui.View):
    """
    Pagination buttons for memory list navigation.

    Features:
    - Prev/Next buttons with automatic disable at boundaries
    - User verification (only the invoking user can interact)
    - 5-minute timeout
    """

    def __init__(
        self,
        user_id: int,
        current_page: int,
        total_pages: int,
        fetch_page: Callable[[int], Awaitable[discord.Embed]],
        timeout: float = 300.0,
    ):
        """
        Initialize pagination view.

        Args:
            user_id: Discord user ID who can interact with this view
            current_page: Current page number (1-indexed)
            total_pages: Total number of pages
            fetch_page: Async callback to fetch embed for a given page
            timeout: View timeout in seconds (default 5 minutes)
        """
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.current_page = current_page
        self.total_pages = total_pages
        self.fetch_page = fetch_page
        self._update_buttons()

    def _update_buttons(self):
        """Update button disabled state based on current page."""
        self.prev_button.disabled = self.current_page <= 1
        self.next_button.disabled = self.current_page >= self.total_pages

    async def _verify_user(self, interaction: discord.Interaction) -> bool:
        """Verify the interaction is from the original user."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This menu belongs to someone else. Use `/memories` to open your own.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️")
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Navigate to the previous page."""
        if not await self._verify_user(interaction):
            return

        self.current_page -= 1
        embed = await self.fetch_page(self.current_page)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️")
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Navigate to the next page."""
        if not await self._verify_user(interaction):
            return

        self.current_page += 1
        embed = await self.fetch_page(self.current_page)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        """Disable buttons when view times out."""
        self.prev_button.disabled = True
        self.next_button.disabled = True


class DeleteConfirmView(discord.ui.View):
    """
    Confirmation dialog for memory deletion.

    Features:
    - Confirm (danger red) and Cancel buttons
    - User verification
    - 60-second timeout
    """

    def __init__(
        self,
        user_id: int,
        memory_id: int,
        on_confirm: Callable[[discord.Interaction, int], Awaitable[None]],
        timeout: float = 60.0,
    ):
        """
        Initialize deletion confirmation view.

        Args:
            user_id: Discord user ID who can interact with this view
            memory_id: Memory ID to delete on confirmation
            on_confirm: Async callback when confirmed (receives interaction and memory_id)
            timeout: View timeout in seconds (default 60 seconds)
        """
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.memory_id = memory_id
        self.on_confirm = on_confirm

    async def _verify_user(self, interaction: discord.Interaction) -> bool:
        """Verify the interaction is from the original user."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This confirmation belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Confirm the deletion."""
        if not await self._verify_user(interaction):
            return

        await self.on_confirm(interaction, self.memory_id)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Cancel the deletion."""
        if not await self._verify_user(interaction):
            return

        await interaction.response.edit_message(
            content="Deletion cancelled.",
            embed=None,
            view=None,
        )
        self.stop()

    async def on_timeout(self):
        """Disable buttons when view times out."""
        self.confirm_button.disabled = True
        self.cancel_button.disabled = True


class MemoryDetailView(discord.ui.View):
    """
    View for memory detail display with optional delete button.

    Features:
    - Delete button (only shown for own memories)
    - Back button to return to list
    - User verification
    """

    def __init__(
        self,
        user_id: int,
        memory_id: int,
        can_delete: bool,
        on_delete: Optional[Callable[[discord.Interaction, int], Awaitable[None]]] = None,
        timeout: float = 300.0,
    ):
        """
        Initialize memory detail view.

        Args:
            user_id: Discord user ID who can interact with this view
            memory_id: Memory ID being viewed
            can_delete: Whether to show delete button
            on_delete: Async callback when delete is clicked
            timeout: View timeout in seconds
        """
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.memory_id = memory_id
        self.on_delete = on_delete

        # Only add delete button if user can delete
        if not can_delete:
            self.remove_item(self.delete_button)

    async def _verify_user(self, interaction: discord.Interaction) -> bool:
        """Verify the interaction is from the original user."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This menu belongs to someone else.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Initiate deletion flow."""
        if not await self._verify_user(interaction):
            return

        if self.on_delete:
            await self.on_delete(interaction, self.memory_id)
        self.stop()

    async def on_timeout(self):
        """Disable buttons when view times out."""
        if hasattr(self, "delete_button"):
            self.delete_button.disabled = True
