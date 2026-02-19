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

v0.9.19: Conversational delivery with context-awareness and memory retrieval.
"""

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import anthropic
import asyncpg
import discord
import pytz
from discord.ext import tasks

from analytics import track

if TYPE_CHECKING:
    from discord_bot import DiscordBot

from .manager import ReminderManager

logger = logging.getLogger("slashAI.reminders.scheduler")

# Model for reminder message generation
REMINDER_MODEL = "claude-sonnet-4-6"


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
        self.db_pool = db_pool
        self.manager = ReminderManager(db_pool)
        self._started = False

        # Initialize Anthropic client for conversational message generation
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self.anthropic_client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else None

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

    async def _get_channel_context(
        self, channel: discord.TextChannel, limit: int = 8
    ) -> str:
        """
        Get recent messages from a channel for context.

        Args:
            channel: Discord channel
            limit: Number of messages to fetch

        Returns:
            Formatted string of recent messages
        """
        try:
            messages = []
            async for msg in channel.history(limit=limit):
                if not msg.author.bot:  # Skip bot messages for cleaner context
                    author = msg.author.display_name
                    content = msg.content[:200] if msg.content else "[no text]"
                    messages.append(f"{author}: {content}")

            if messages:
                messages.reverse()  # Chronological order
                return "\n".join(messages)
            return ""
        except Exception as e:
            logger.warning(f"Failed to get channel context: {e}")
            return ""

    async def _get_user_memories(
        self,
        user_id: int,
        reminder_content: str,
        channel: Optional[discord.abc.Messageable] = None,
    ) -> str:
        """
        Retrieve relevant memories about the user for personalization.

        Args:
            user_id: Discord user ID
            reminder_content: The reminder content (used for semantic search)
            channel: Channel for privacy filtering

        Returns:
            Formatted string of relevant memories
        """
        try:
            if not hasattr(self.bot, 'memory_manager') or self.bot.memory_manager is None:
                return ""

            # Use the reminder content as the query for semantic search
            memories = await self.bot.memory_manager.retrieve(
                user_id, reminder_content, channel, limit=3
            )

            if memories:
                memory_texts = [m.get("content", "") for m in memories if m.get("content")]
                if memory_texts:
                    return "\n".join(f"- {m}" for m in memory_texts)
            return ""
        except Exception as e:
            logger.warning(f"Failed to get user memories: {e}")
            return ""

    def _get_recurrence_description(self, cron_expression: Optional[str]) -> str:
        """
        Convert CRON expression to human-readable recurrence description.

        Args:
            cron_expression: CRON expression or None for one-time

        Returns:
            Human-readable description
        """
        if not cron_expression:
            return "one-time"

        # Common patterns
        cron_descriptions = {
            "0 * * * *": "hourly",
            "0 9 * * *": "daily",
            "0 9 * * 1": "weekly",
            "0 9 * * 1-5": "weekday",
            "0 9 1 * *": "monthly",
        }

        if cron_expression in cron_descriptions:
            return cron_descriptions[cron_expression]

        # Try to infer from pattern
        parts = cron_expression.split()
        if len(parts) == 5:
            minute, hour, dom, month, dow = parts
            if dom == "*" and month == "*":
                if dow == "*":
                    return "daily"
                elif dow == "1-5":
                    return "weekday"
                elif dow in "0123456":
                    return "weekly"

        return f"recurring ({cron_expression})"

    def _get_timezone_short(self, timezone: str) -> str:
        """
        Get a short timezone abbreviation.

        Args:
            timezone: IANA timezone name

        Returns:
            Short timezone abbreviation (e.g., PST, EST)
        """
        try:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            return now.strftime("%Z")
        except Exception:
            return timezone

    async def _generate_reminder_message(
        self,
        reminder: dict,
        user: discord.User,
        channel: Optional[discord.TextChannel] = None,
    ) -> str:
        """
        Generate a conversational reminder message using Claude.

        Args:
            reminder: Reminder dict from database
            user: Discord user to remind
            channel: Channel for delivery (None for DM)

        Returns:
            Conversational reminder message
        """
        content = reminder["content"]
        timezone = reminder["timezone"]
        cron_expression = reminder.get("cron_expression")
        is_channel = channel is not None

        # Get current time in user's timezone
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)
        time_str = now.strftime("%I:%M %p").lstrip("0")
        tz_short = self._get_timezone_short(timezone)
        recurrence = self._get_recurrence_description(cron_expression)

        # Fallback template if Anthropic client unavailable
        fallback_message = self._build_fallback_message(
            user, content, time_str, tz_short, recurrence, is_channel
        )

        if not self.anthropic_client:
            return fallback_message

        try:
            # Gather context
            channel_context = ""
            if is_channel and channel:
                channel_context = await self._get_channel_context(channel)

            memory_context = await self._get_user_memories(
                reminder["user_id"], content, channel
            )

            # Build prompt
            prompt_parts = [
                f"You're delivering a scheduled reminder to {user.display_name}.",
                f"",
                f"Reminder content: {content}",
                f"Current time: {time_str} {tz_short}",
                f"Recurrence: {recurrence}",
            ]

            if channel_context:
                prompt_parts.append(f"\nRecent channel conversation:\n{channel_context}")

            if memory_context:
                prompt_parts.append(f"\nRelevant context about this user:\n{memory_context}")

            # Build mention instruction
            if is_channel:
                mention_instruction = f"- Start with the Discord mention <@{user.id}> (use this exact text)"
            else:
                mention_instruction = "- No @mention needed (this is a DM)"

            prompt_parts.extend([
                "",
                "Generate a natural, conversational reminder message. Guidelines:",
                mention_instruction,
                "- Include the reminder content naturally",
                f"- Mention the time with timezone: {time_str} {tz_short}",
                f"- For recurring reminders, note the frequency naturally (e.g., 'your {recurrence} reminder')" if cron_expression else "",
                "- Keep personality warm and conversational",
                "- Be contextually appropriate to any ongoing conversation",
                "- Keep it concise (1-3 sentences)",
                "",
                "Output ONLY the reminder message, nothing else.",
            ])

            prompt = "\n".join(p for p in prompt_parts if p)

            response = await self.anthropic_client.messages.create(
                model=REMINDER_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )

            if response.content and response.content[0].type == "text":
                message = response.content[0].text.strip()
                return message

            return fallback_message

        except Exception as e:
            logger.warning(f"Failed to generate reminder message: {e}")
            return fallback_message

    def _build_fallback_message(
        self,
        user: discord.User,
        content: str,
        time_str: str,
        tz_short: str,
        recurrence: str,
        is_channel: bool,
    ) -> str:
        """
        Build a simple fallback message if Claude API fails.

        Args:
            user: Discord user
            content: Reminder content
            time_str: Formatted time string
            tz_short: Timezone abbreviation
            recurrence: Recurrence description
            is_channel: Whether this is channel delivery

        Returns:
            Simple reminder message
        """
        mention = f"<@{user.id}>" if is_channel else "Hey"
        if recurrence == "one-time":
            return f"{mention}, reminder: {content} ({time_str} {tz_short})"
        else:
            return f"{mention}, your {recurrence} reminder: {content} ({time_str} {tz_short})"

    async def _deliver_reminder(self, reminder: dict) -> None:
        """
        Deliver a single reminder using conversational messages.

        Args:
            reminder: Reminder dict from database
        """
        reminder_id = reminder["id"]
        user_id = reminder["user_id"]
        is_channel_delivery = reminder["is_channel_delivery"]
        delivery_channel_id = reminder["delivery_channel_id"]

        try:
            # Get the user
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.NotFound:
                    await self.manager.mark_failed_immediate(
                        reminder_id, "User not found"
                    )
                    return

            if is_channel_delivery and delivery_channel_id:
                # Channel delivery
                channel = self.bot.get_channel(delivery_channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(delivery_channel_id)
                    except discord.NotFound:
                        await self.manager.mark_failed_immediate(
                            reminder_id, "Channel not found (deleted)"
                        )
                        return
                    except discord.Forbidden:
                        await self.manager.mark_failed_immediate(
                            reminder_id, "No access to channel"
                        )
                        return

                # Generate conversational message with channel context
                message = await self._generate_reminder_message(reminder, user, channel)
                await channel.send(message)
                logger.info(f"Delivered reminder {reminder_id} to channel {delivery_channel_id}")
            else:
                # DM delivery
                try:
                    # Generate conversational message (no channel context for DMs)
                    message = await self._generate_reminder_message(reminder, user, None)
                    await user.send(message)
                    logger.info(f"Delivered reminder {reminder_id} to user {user_id} via DM")
                except discord.Forbidden:
                    await self.manager.mark_executed(
                        reminder_id,
                        success=False,
                        error_message="User has DMs disabled",
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
                    "used_ai_generation": self.anthropic_client is not None,
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
