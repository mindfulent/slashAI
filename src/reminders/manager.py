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
Reminder Manager Module

Handles database operations for scheduled reminders.
"""

import logging
from datetime import datetime
from typing import Optional

import asyncpg
import pytz

from .time_parser import ParsedTime, calculate_next_execution, validate_timezone

logger = logging.getLogger("slashAI.reminders.manager")


class ReminderManager:
    """
    Manages database operations for scheduled reminders.

    Provides methods to create, list, update, and cancel reminders,
    as well as manage user timezone preferences.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        """
        Initialize the reminder manager.

        Args:
            db_pool: asyncpg connection pool
        """
        self.db = db_pool

    async def create_reminder(
        self,
        user_id: int,
        content: str,
        parsed_time: ParsedTime,
        delivery_channel_id: Optional[int] = None,
        is_channel_delivery: bool = False,
    ) -> int:
        """
        Create a new reminder.

        Args:
            user_id: Discord user ID
            content: Reminder message content
            parsed_time: Parsed time expression result
            delivery_channel_id: Channel ID for delivery (None = DM)
            is_channel_delivery: True if delivering to channel

        Returns:
            The ID of the created reminder
        """
        row = await self.db.fetchrow(
            """
            INSERT INTO scheduled_reminders (
                user_id, content, cron_expression, next_execution_at,
                timezone, delivery_channel_id, is_channel_delivery
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            user_id,
            content,
            parsed_time.cron_expression,
            parsed_time.next_execution,
            parsed_time.timezone,
            delivery_channel_id,
            is_channel_delivery,
        )

        reminder_id = row["id"]
        logger.info(
            f"Created reminder {reminder_id} for user {user_id}: "
            f"next={parsed_time.next_execution}, recurring={parsed_time.is_recurring}"
        )
        return reminder_id

    async def list_reminders(
        self,
        user_id: int,
        include_completed: bool = False,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        List reminders for a user.

        Args:
            user_id: Discord user ID
            include_completed: Include completed/failed reminders
            limit: Maximum results per page
            offset: Offset for pagination

        Returns:
            Tuple of (list of reminder dicts, total count)
        """
        # Build status filter
        if include_completed:
            status_filter = "1=1"  # No filter
        else:
            status_filter = "status IN ('active', 'paused')"

        # Get total count
        count_row = await self.db.fetchrow(
            f"""
            SELECT COUNT(*) as count FROM scheduled_reminders
            WHERE user_id = $1 AND {status_filter}
            """,
            user_id,
        )
        total = count_row["count"]

        # Get paginated results
        rows = await self.db.fetch(
            f"""
            SELECT id, content, cron_expression, next_execution_at,
                   timezone, delivery_channel_id, is_channel_delivery,
                   status, last_executed_at, execution_count,
                   created_at, updated_at
            FROM scheduled_reminders
            WHERE user_id = $1 AND {status_filter}
            ORDER BY next_execution_at ASC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            limit,
            offset,
        )

        reminders = [dict(row) for row in rows]
        return reminders, total

    async def get_reminder(self, reminder_id: int) -> Optional[dict]:
        """
        Get a reminder by ID.

        Args:
            reminder_id: Reminder ID

        Returns:
            Reminder dict or None if not found
        """
        row = await self.db.fetchrow(
            """
            SELECT id, user_id, content, cron_expression, next_execution_at,
                   timezone, delivery_channel_id, is_channel_delivery,
                   status, last_executed_at, execution_count, failure_count,
                   last_error, created_at, updated_at
            FROM scheduled_reminders
            WHERE id = $1
            """,
            reminder_id,
        )

        return dict(row) if row else None

    async def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        """
        Cancel (delete) a reminder if the user owns it.

        Args:
            reminder_id: Reminder ID
            user_id: Discord user ID (for ownership check)

        Returns:
            True if deleted, False if not found or not owned
        """
        result = await self.db.execute(
            """
            DELETE FROM scheduled_reminders
            WHERE id = $1 AND user_id = $2
            """,
            reminder_id,
            user_id,
        )

        deleted = result == "DELETE 1"
        if deleted:
            logger.info(f"Cancelled reminder {reminder_id} for user {user_id}")
        return deleted

    async def pause_reminder(self, reminder_id: int, user_id: int) -> bool:
        """
        Pause a recurring reminder.

        Args:
            reminder_id: Reminder ID
            user_id: Discord user ID (for ownership check)

        Returns:
            True if paused, False if not found, not owned, or not recurring
        """
        result = await self.db.execute(
            """
            UPDATE scheduled_reminders
            SET status = 'paused', updated_at = NOW()
            WHERE id = $1 AND user_id = $2
              AND cron_expression IS NOT NULL
              AND status = 'active'
            """,
            reminder_id,
            user_id,
        )

        paused = result == "UPDATE 1"
        if paused:
            logger.info(f"Paused reminder {reminder_id} for user {user_id}")
        return paused

    async def resume_reminder(self, reminder_id: int, user_id: int) -> bool:
        """
        Resume a paused reminder.

        Args:
            reminder_id: Reminder ID
            user_id: Discord user ID (for ownership check)

        Returns:
            True if resumed, False if not found, not owned, or not paused
        """
        # First get the reminder to recalculate next execution
        reminder = await self.db.fetchrow(
            """
            SELECT cron_expression, timezone FROM scheduled_reminders
            WHERE id = $1 AND user_id = $2 AND status = 'paused'
            """,
            reminder_id,
            user_id,
        )

        if not reminder or not reminder["cron_expression"]:
            return False

        # Calculate next execution from now
        user_tz = pytz.timezone(reminder["timezone"])
        next_exec = calculate_next_execution(reminder["cron_expression"], user_tz)

        result = await self.db.execute(
            """
            UPDATE scheduled_reminders
            SET status = 'active', next_execution_at = $3, updated_at = NOW()
            WHERE id = $1 AND user_id = $2 AND status = 'paused'
            """,
            reminder_id,
            user_id,
            next_exec,
        )

        resumed = result == "UPDATE 1"
        if resumed:
            logger.info(f"Resumed reminder {reminder_id} for user {user_id}")
        return resumed

    async def get_user_timezone(self, user_id: int) -> str:
        """
        Get a user's timezone preference.

        Args:
            user_id: Discord user ID

        Returns:
            Timezone name (defaults to 'UTC')
        """
        row = await self.db.fetchrow(
            """
            SELECT timezone FROM user_settings
            WHERE user_id = $1
            """,
            user_id,
        )

        return row["timezone"] if row else "UTC"

    async def set_user_timezone(self, user_id: int, timezone: str) -> bool:
        """
        Set a user's timezone preference.

        Args:
            user_id: Discord user ID
            timezone: IANA timezone name

        Returns:
            True if set successfully, False if invalid timezone
        """
        if not validate_timezone(timezone):
            return False

        await self.db.execute(
            """
            INSERT INTO user_settings (user_id, timezone, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET timezone = $2, updated_at = NOW()
            """,
            user_id,
            timezone,
        )

        logger.info(f"Set timezone for user {user_id}: {timezone}")
        return True

    async def has_user_timezone(self, user_id: int) -> bool:
        """
        Check if a user has set their timezone preference.

        Args:
            user_id: Discord user ID

        Returns:
            True if user has a timezone set, False otherwise
        """
        row = await self.db.fetchrow(
            """
            SELECT 1 FROM user_settings
            WHERE user_id = $1
            """,
            user_id,
        )
        return row is not None

    # =========================================================================
    # Scheduler-facing methods
    # =========================================================================

    async def get_due_reminders(self) -> list[dict]:
        """
        Get all reminders that are due for execution.

        Returns:
            List of reminder dicts that should be delivered now
        """
        now = datetime.now(pytz.UTC)

        rows = await self.db.fetch(
            """
            SELECT id, user_id, content, cron_expression, next_execution_at,
                   timezone, delivery_channel_id, is_channel_delivery,
                   execution_count, failure_count
            FROM scheduled_reminders
            WHERE status = 'active' AND next_execution_at <= $1
            ORDER BY next_execution_at ASC
            LIMIT 100
            """,
            now,
        )

        return [dict(row) for row in rows]

    async def mark_executed(
        self,
        reminder_id: int,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Mark a reminder as executed and update its state.

        For recurring reminders: calculates next execution time.
        For one-time reminders: marks as completed.
        For failures: increments failure count, marks failed after 5 attempts.

        Args:
            reminder_id: Reminder ID
            success: Whether delivery succeeded
            error_message: Error message if failed
        """
        reminder = await self.get_reminder(reminder_id)
        if not reminder:
            return

        now = datetime.now(pytz.UTC)

        if success:
            # Successful delivery
            if reminder["cron_expression"]:
                # Recurring: calculate next execution
                user_tz = pytz.timezone(reminder["timezone"])
                next_exec = calculate_next_execution(reminder["cron_expression"], user_tz)

                await self.db.execute(
                    """
                    UPDATE scheduled_reminders
                    SET last_executed_at = $2,
                        next_execution_at = $3,
                        execution_count = execution_count + 1,
                        failure_count = 0,
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    reminder_id,
                    now,
                    next_exec,
                )
                logger.info(f"Reminder {reminder_id} executed, next at {next_exec}")
            else:
                # One-time: mark completed
                await self.db.execute(
                    """
                    UPDATE scheduled_reminders
                    SET status = 'completed',
                        last_executed_at = $2,
                        execution_count = execution_count + 1,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    reminder_id,
                    now,
                )
                logger.info(f"One-time reminder {reminder_id} completed")
        else:
            # Failed delivery
            new_failure_count = reminder["failure_count"] + 1
            max_failures = 5

            if new_failure_count >= max_failures:
                # Mark as failed after too many attempts
                await self.db.execute(
                    """
                    UPDATE scheduled_reminders
                    SET status = 'failed',
                        failure_count = $2,
                        last_error = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    reminder_id,
                    new_failure_count,
                    error_message,
                )
                logger.warning(
                    f"Reminder {reminder_id} marked as failed after {max_failures} attempts"
                )
            else:
                # Increment failure count but keep active
                await self.db.execute(
                    """
                    UPDATE scheduled_reminders
                    SET failure_count = $2,
                        last_error = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    reminder_id,
                    new_failure_count,
                    error_message,
                )
                logger.warning(
                    f"Reminder {reminder_id} failed ({new_failure_count}/{max_failures}): {error_message}"
                )

    async def mark_failed_immediate(
        self,
        reminder_id: int,
        error_message: str,
    ) -> None:
        """
        Immediately mark a reminder as failed (e.g., channel deleted).

        Args:
            reminder_id: Reminder ID
            error_message: Error message
        """
        await self.db.execute(
            """
            UPDATE scheduled_reminders
            SET status = 'failed',
                last_error = $2,
                updated_at = NOW()
            WHERE id = $1
            """,
            reminder_id,
            error_message,
        )
        logger.warning(f"Reminder {reminder_id} immediately failed: {error_message}")
