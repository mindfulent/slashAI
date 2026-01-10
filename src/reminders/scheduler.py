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
Reminder Scheduler Module

Background task loop for checking and delivering scheduled reminders.
Uses discord.ext.tasks for reliable scheduling.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import asyncpg
import discord
import pytz
from discord.ext import tasks

from analytics import track

if TYPE_CHECKING:
    from discord_bot import DiscordBot

from .manager import ReminderManager

logger = logging.getLogger("slashAI.reminders.scheduler")


class ReminderScheduler:
    """
    Background scheduler for delivering reminders.

    Runs a loop every 60 seconds to check for due reminders and deliver them.
    Handles both DM and channel delivery, with retry logic for failures.
    """

    def __init__(self, bot: "DiscordBot", db_pool: asyncpg.Pool):
        """
        Initialize the reminder scheduler.

        Args:
            bot: Discord bot instance
            db_pool: asyncpg connection pool
        """
        self.bot = bot
        self.manager = ReminderManager(db_pool)
        self._started = False

    def start(self) -> None:
        """Start the scheduler loop."""
        if not self._started:
            self._check_reminders.start()
            self._started = True
            logger.info("Reminder scheduler started")

    def stop(self) -> None:
        """Stop the scheduler loop."""
        if self._started:
            self._check_reminders.cancel()
            self._started = False
            logger.info("Reminder scheduler stopped")

    @tasks.loop(seconds=60)
    async def _check_reminders(self) -> None:
        """Check for due reminders and deliver them."""
        try:
            due_reminders = await self.manager.get_due_reminders()

            if due_reminders:
                logger.info(f"Processing {len(due_reminders)} due reminder(s)")

            for reminder in due_reminders:
                await self._deliver_reminder(reminder)

        except Exception as e:
            logger.error(f"Error in reminder scheduler loop: {e}", exc_info=True)
            # Analytics: Track scheduler error
            track(
                "scheduler_error",
                "error",
                properties={
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:200],
                },
            )

    @_check_reminders.before_loop
    async def _before_check(self) -> None:
        """Wait for the bot to be ready before starting the loop."""
        await self.bot.wait_until_ready()
        logger.info("Reminder scheduler ready, starting loop")

    async def _deliver_reminder(self, reminder: dict) -> None:
        """
        Deliver a single reminder.

        Args:
            reminder: Reminder dict from database
        """
        reminder_id = reminder["id"]
        user_id = reminder["user_id"]
        content = reminder["content"]
        is_channel_delivery = reminder["is_channel_delivery"]
        delivery_channel_id = reminder["delivery_channel_id"]

        try:
            # Build the reminder embed
            embed = self._build_reminder_embed(reminder)

            if is_channel_delivery and delivery_channel_id:
                # Channel delivery
                channel = self.bot.get_channel(delivery_channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(delivery_channel_id)
                    except discord.NotFound:
                        # Channel deleted - mark failed immediately
                        await self.manager.mark_failed_immediate(
                            reminder_id, "Channel not found (deleted)"
                        )
                        return
                    except discord.Forbidden:
                        await self.manager.mark_failed_immediate(
                            reminder_id, "No access to channel"
                        )
                        return

                await channel.send(embed=embed)
                logger.info(f"Delivered reminder {reminder_id} to channel {delivery_channel_id}")
            else:
                # DM delivery
                try:
                    user = self.bot.get_user(user_id)
                    if user is None:
                        user = await self.bot.fetch_user(user_id)

                    await user.send(embed=embed)
                    logger.info(f"Delivered reminder {reminder_id} to user {user_id} via DM")
                except discord.Forbidden:
                    # User has DMs closed - mark as failed
                    await self.manager.mark_executed(
                        reminder_id,
                        success=False,
                        error_message="User has DMs disabled",
                    )
                    return
                except discord.NotFound:
                    await self.manager.mark_failed_immediate(
                        reminder_id, "User not found"
                    )
                    return

            # Mark as successfully executed
            await self.manager.mark_executed(reminder_id, success=True)

            # Analytics: Track reminder delivered
            track(
                "reminder_delivered",
                "reminder",
                user_id=user_id,
                channel_id=delivery_channel_id if is_channel_delivery else None,
                properties={
                    "reminder_id": reminder_id,
                    "delivery_type": "channel" if is_channel_delivery else "dm",
                    "is_recurring": reminder["cron_expression"] is not None,
                },
            )

        except Exception as e:
            logger.error(f"Failed to deliver reminder {reminder_id}: {e}", exc_info=True)
            await self.manager.mark_executed(
                reminder_id,
                success=False,
                error_message=str(e)[:200],
            )

            # Analytics: Track delivery error
            track(
                "reminder_delivery_error",
                "error",
                user_id=user_id,
                properties={
                    "reminder_id": reminder_id,
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:200],
                },
            )

    def _build_reminder_embed(self, reminder: dict) -> discord.Embed:
        """
        Build the embed for a reminder delivery.

        Args:
            reminder: Reminder dict

        Returns:
            Discord embed
        """
        embed = discord.Embed(
            title="Reminder",
            description=reminder["content"],
            color=discord.Color.blue(),
            timestamp=datetime.now(pytz.UTC),
        )

        # Add recurrence info if recurring
        if reminder["cron_expression"]:
            embed.add_field(
                name="Schedule",
                value=f"`{reminder['cron_expression']}`",
                inline=True,
            )

        embed.set_footer(text=f"Reminder ID: {reminder['id']}")

        return embed
