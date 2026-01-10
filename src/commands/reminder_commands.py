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
Reminder Slash Commands

Discord slash commands for managing scheduled reminders.
"""

import logging
from datetime import datetime
from typing import Optional

import asyncpg
import discord
import pytz
from discord import app_commands
from discord.ext import commands

from analytics import track
from reminders import ReminderManager, parse_time_expression, TimeParseError, CRON_PRESETS

logger = logging.getLogger("slashAI.commands.reminder")

# Common timezones for autocomplete
COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Anchorage",
    "Pacific/Honolulu",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Moscow",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Asia/Singapore",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Australia/Sydney",
    "Australia/Perth",
    "Pacific/Auckland",
]

# Page size for reminder lists
PAGE_SIZE = 10


class ReminderCommands(commands.Cog):
    """
    Slash commands for reminder management.

    Commands:
    - /remind set - Create a new reminder
    - /remind list - List your reminders
    - /remind cancel - Cancel a reminder
    - /remind pause - Pause a recurring reminder
    - /remind resume - Resume a paused reminder
    - /remind timezone - Set your timezone
    """

    remind_group = app_commands.Group(
        name="remind",
        description="Manage your scheduled reminders",
    )

    def __init__(
        self,
        bot: commands.Bot,
        db_pool: asyncpg.Pool,
        reminder_manager: ReminderManager,
        owner_id: Optional[str] = None,
    ):
        self.bot = bot
        self.db = db_pool
        self.manager = reminder_manager
        self.owner_id = int(owner_id) if owner_id else None

    # =========================================================================
    # /remind set
    # =========================================================================

    @remind_group.command(name="set")
    @app_commands.describe(
        message="The reminder message content",
        time="When to remind (e.g., 'in 2 hours', 'tomorrow at 10am', 'every weekday at 9am', '0 10 * * *')",
        channel="Optional: Channel to post in (admin only, defaults to DM)",
    )
    async def set_reminder(
        self,
        interaction: discord.Interaction,
        message: str,
        time: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Create a new reminder."""
        await interaction.response.defer(ephemeral=True)

        # Analytics: Track command usage
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "remind", "subcommand": "set"},
        )

        user_id = interaction.user.id

        # Check channel delivery permission (admin only)
        is_channel_delivery = False
        delivery_channel_id = None

        if channel is not None:
            if self.owner_id and user_id == self.owner_id:
                is_channel_delivery = True
                delivery_channel_id = channel.id
            else:
                await interaction.followup.send(
                    "Channel delivery is only available to the bot admin. "
                    "Your reminder will be delivered via DM instead.",
                    ephemeral=True,
                )

        # Get user's timezone
        user_tz = await self.manager.get_user_timezone(user_id)

        # Parse the time expression
        try:
            parsed = parse_time_expression(time, user_tz)
        except TimeParseError as e:
            await interaction.followup.send(
                f"Could not parse time: {e}\n\n"
                "**Examples:**\n"
                "- `in 2 hours`\n"
                "- `tomorrow at 10am`\n"
                "- `next Monday 3pm`\n"
                "- `every weekday at 9am`\n"
                "- `daily` (9am default)\n"
                "- `0 10 * * *` (CRON: 10am daily)",
                ephemeral=True,
            )
            return

        # Create the reminder
        reminder_id = await self.manager.create_reminder(
            user_id=user_id,
            content=message,
            parsed_time=parsed,
            delivery_channel_id=delivery_channel_id,
            is_channel_delivery=is_channel_delivery,
        )

        # Build response embed
        embed = discord.Embed(
            title="Reminder Created",
            color=discord.Color.green(),
        )

        embed.add_field(name="ID", value=str(reminder_id), inline=True)
        embed.add_field(name="Message", value=message[:100] + "..." if len(message) > 100 else message, inline=False)

        # Format schedule
        if parsed.is_recurring:
            schedule_str = f"Recurring (`{parsed.cron_expression}`)"
        else:
            schedule_str = "One-time"
        embed.add_field(name="Schedule", value=schedule_str, inline=True)

        # Format next execution in user's timezone
        user_tz_obj = pytz.timezone(user_tz)
        next_local = parsed.next_execution.astimezone(user_tz_obj)
        embed.add_field(
            name="Next",
            value=next_local.strftime("%Y-%m-%d %H:%M %Z"),
            inline=True,
        )

        # Delivery location
        if is_channel_delivery:
            embed.add_field(name="Delivery", value=f"#{channel.name}", inline=True)
        else:
            embed.add_field(name="Delivery", value="DM", inline=True)

        embed.add_field(name="Timezone", value=user_tz, inline=True)

        embed.set_footer(text="Use /remind list to see all your reminders")

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Analytics: Track reminder created
        track(
            "reminder_created",
            "reminder",
            user_id=user_id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={
                "reminder_id": reminder_id,
                "is_recurring": parsed.is_recurring,
                "delivery_type": "channel" if is_channel_delivery else "dm",
            },
        )

    # =========================================================================
    # /remind list
    # =========================================================================

    @remind_group.command(name="list")
    @app_commands.describe(
        include_completed="Show completed/failed reminders too (default: false)",
        page="Page number (default: 1)",
    )
    async def list_reminders(
        self,
        interaction: discord.Interaction,
        include_completed: bool = False,
        page: int = 1,
    ):
        """List your reminders."""
        await interaction.response.defer(ephemeral=True)

        # Analytics
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "remind", "subcommand": "list", "page": page},
        )

        user_id = interaction.user.id
        offset = (page - 1) * PAGE_SIZE

        reminders, total = await self.manager.list_reminders(
            user_id, include_completed=include_completed, limit=PAGE_SIZE, offset=offset
        )

        if total == 0:
            await interaction.followup.send(
                "You don't have any reminders. Use `/remind set` to create one!",
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

        # Get user timezone for display
        user_tz = await self.manager.get_user_timezone(user_id)
        user_tz_obj = pytz.timezone(user_tz)

        # Build embed
        embed = discord.Embed(
            title="Your Reminders",
            description=f"Page {page}/{total_pages} - {total} total reminders",
            color=discord.Color.blue(),
        )

        for rem in reminders:
            # Status icon
            status_icons = {
                "active": "",
                "paused": " (paused)",
                "completed": "",
                "failed": "",
            }
            status_icon = status_icons.get(rem["status"], "")

            # Format next execution
            next_exec = rem["next_execution_at"]
            if next_exec:
                next_local = next_exec.astimezone(user_tz_obj)
                next_str = next_local.strftime("%m/%d %H:%M")
            else:
                next_str = "N/A"

            # Truncate content
            content = rem["content"]
            if len(content) > 50:
                content = content[:47] + "..."

            # Recurrence indicator
            recur = "" if rem["cron_expression"] else ""

            embed.add_field(
                name=f"[{rem['id']}] {recur}{status_icon} {content}",
                value=f"Next: {next_str} | Runs: {rem['execution_count']}",
                inline=False,
            )

        embed.set_footer(text=f"Timezone: {user_tz} | Use /remind cancel <id> to remove")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # =========================================================================
    # /remind cancel
    # =========================================================================

    @remind_group.command(name="cancel")
    @app_commands.describe(reminder_id="The reminder ID to cancel")
    async def cancel_reminder(
        self,
        interaction: discord.Interaction,
        reminder_id: int,
    ):
        """Cancel a reminder."""
        # Analytics
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "remind", "subcommand": "cancel", "reminder_id": reminder_id},
        )

        user_id = interaction.user.id

        success = await self.manager.cancel_reminder(reminder_id, user_id)

        if success:
            await interaction.response.send_message(
                f"Reminder #{reminder_id} has been cancelled.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Reminder #{reminder_id} not found or you don't own it.",
                ephemeral=True,
            )

    # =========================================================================
    # /remind pause
    # =========================================================================

    @remind_group.command(name="pause")
    @app_commands.describe(reminder_id="The recurring reminder ID to pause")
    async def pause_reminder(
        self,
        interaction: discord.Interaction,
        reminder_id: int,
    ):
        """Pause a recurring reminder."""
        # Analytics
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "remind", "subcommand": "pause", "reminder_id": reminder_id},
        )

        user_id = interaction.user.id

        success = await self.manager.pause_reminder(reminder_id, user_id)

        if success:
            await interaction.response.send_message(
                f"Reminder #{reminder_id} has been paused. Use `/remind resume` to resume it.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Could not pause reminder #{reminder_id}. "
                "It may not exist, not be yours, not be recurring, or already be paused.",
                ephemeral=True,
            )

    # =========================================================================
    # /remind resume
    # =========================================================================

    @remind_group.command(name="resume")
    @app_commands.describe(reminder_id="The paused reminder ID to resume")
    async def resume_reminder(
        self,
        interaction: discord.Interaction,
        reminder_id: int,
    ):
        """Resume a paused reminder."""
        # Analytics
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "remind", "subcommand": "resume", "reminder_id": reminder_id},
        )

        user_id = interaction.user.id

        success = await self.manager.resume_reminder(reminder_id, user_id)

        if success:
            await interaction.response.send_message(
                f"Reminder #{reminder_id} has been resumed.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Could not resume reminder #{reminder_id}. "
                "It may not exist, not be yours, or not be paused.",
                ephemeral=True,
            )

    # =========================================================================
    # /remind timezone
    # =========================================================================

    @remind_group.command(name="timezone")
    @app_commands.describe(timezone="Your timezone (e.g., America/Los_Angeles, Europe/London)")
    async def set_timezone(
        self,
        interaction: discord.Interaction,
        timezone: str,
    ):
        """Set your timezone for reminders."""
        # Analytics
        track(
            "command_used",
            "command",
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild.id if interaction.guild else None,
            properties={"command_name": "remind", "subcommand": "timezone"},
        )

        user_id = interaction.user.id

        success = await self.manager.set_user_timezone(user_id, timezone)

        if success:
            await interaction.response.send_message(
                f"Your timezone has been set to **{timezone}**.\n"
                "All future reminders will use this timezone.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Invalid timezone: `{timezone}`\n\n"
                "**Common timezones:**\n"
                "- `America/New_York` (Eastern)\n"
                "- `America/Chicago` (Central)\n"
                "- `America/Denver` (Mountain)\n"
                "- `America/Los_Angeles` (Pacific)\n"
                "- `Europe/London`\n"
                "- `Europe/Paris`\n"
                "- `Asia/Tokyo`\n"
                "- `UTC`\n\n"
                "Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
                ephemeral=True,
            )

    @set_timezone.autocomplete("timezone")
    async def timezone_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for timezone parameter."""
        # Filter timezones that match the current input
        current_lower = current.lower()
        matches = [
            tz for tz in COMMON_TIMEZONES
            if current_lower in tz.lower()
        ]

        # If no matches from common, search all pytz timezones
        if not matches and len(current) >= 2:
            matches = [
                tz for tz in pytz.common_timezones
                if current_lower in tz.lower()
            ][:25]  # Limit to 25

        return [
            app_commands.Choice(name=tz, value=tz)
            for tz in matches[:25]
        ]


async def setup(bot: commands.Bot):
    """
    Standard discord.py cog setup function.

    Note: This cog requires additional parameters, so it's loaded
    manually in discord_bot.py rather than using this function.
    """
    pass
