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
Time Parser Module

Parses natural language time expressions and CRON expressions for reminders.
Supports both one-time ("in 2 hours", "tomorrow at 10am") and recurring
("every weekday at 9am", "0 10 * * *") schedules.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import dateparser
import pytz
from croniter import croniter

logger = logging.getLogger("slashAI.reminders.time_parser")

# CRON presets for common schedules
CRON_PRESETS = {
    "hourly": "0 * * * *",
    "daily": "0 9 * * *",
    "weekly": "0 9 * * 1",
    "weekdays": "0 9 * * 1-5",
    "weekends": "0 10 * * 0,6",
    "monthly": "0 9 1 * *",
}

# Natural language patterns that indicate recurring schedules
RECURRING_PATTERNS = [
    (r"every\s+hour", "hourly"),
    (r"every\s+day", "daily"),
    (r"every\s+week", "weekly"),
    (r"every\s+weekday", "weekdays"),
    (r"every\s+weekend", "weekends"),
    (r"every\s+month", "monthly"),
    (r"every\s+(\d+)\s+hours?", None),  # Custom: every N hours
    (r"every\s+(\d+)\s+minutes?", None),  # Custom: every N minutes
    (r"every\s+(\d+)\s+days?", None),  # Custom: every N days
]


@dataclass
class ParsedTime:
    """Result of parsing a time expression."""

    next_execution: datetime  # UTC timestamp
    cron_expression: Optional[str]  # None for one-time
    is_recurring: bool
    original_input: str
    timezone: str


class TimeParseError(Exception):
    """Raised when a time expression cannot be parsed."""

    pass


def validate_timezone(tz_name: str) -> bool:
    """
    Validate that a timezone name is valid.

    Args:
        tz_name: IANA timezone name (e.g., "America/Los_Angeles")

    Returns:
        True if valid, False otherwise
    """
    try:
        pytz.timezone(tz_name)
        return True
    except pytz.UnknownTimeZoneError:
        return False


def _is_cron_expression(expr: str) -> bool:
    """Check if expression looks like a CRON expression."""
    # CRON has 5 space-separated fields
    parts = expr.strip().split()
    if len(parts) != 5:
        return False

    # Basic validation: each part should be numbers, *, -, /, or ,
    cron_pattern = re.compile(r'^[\d\*\-\/\,]+$')
    return all(cron_pattern.match(part) for part in parts)


def _parse_recurring_natural(expr: str, user_tz: pytz.BaseTzInfo) -> Optional[tuple[str, datetime]]:
    """
    Parse natural language recurring expressions.

    Args:
        expr: The expression to parse
        user_tz: User's timezone

    Returns:
        Tuple of (cron_expression, next_execution) or None if not a recurring pattern
    """
    expr_lower = expr.lower().strip()

    # Check preset keywords first
    for keyword, preset_key in [
        ("hourly", "hourly"),
        ("daily", "daily"),
        ("weekly", "weekly"),
        ("weekdays", "weekdays"),
        ("weekends", "weekends"),
        ("monthly", "monthly"),
    ]:
        if keyword in expr_lower:
            cron_expr = CRON_PRESETS[preset_key]
            # Extract time if specified (e.g., "daily at 3pm")
            time_match = re.search(r'at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', expr_lower)
            if time_match:
                cron_expr = _adjust_cron_time(cron_expr, time_match.group(1), user_tz)
            next_exec = calculate_next_execution(cron_expr, user_tz)
            return (cron_expr, next_exec)

    # Check "every X" patterns
    # Every N hours
    match = re.search(r'every\s+(\d+)\s+hours?', expr_lower)
    if match:
        hours = int(match.group(1))
        if hours < 1 or hours > 24:
            raise TimeParseError(f"Invalid hour interval: {hours}. Must be 1-24.")
        # Create cron that runs every N hours
        cron_expr = f"0 */{hours} * * *"
        next_exec = calculate_next_execution(cron_expr, user_tz)
        return (cron_expr, next_exec)

    # Every N minutes
    match = re.search(r'every\s+(\d+)\s+minutes?', expr_lower)
    if match:
        minutes = int(match.group(1))
        if minutes < 1 or minutes > 60:
            raise TimeParseError(f"Invalid minute interval: {minutes}. Must be 1-60.")
        cron_expr = f"*/{minutes} * * * *"
        next_exec = calculate_next_execution(cron_expr, user_tz)
        return (cron_expr, next_exec)

    # Every N days
    match = re.search(r'every\s+(\d+)\s+days?', expr_lower)
    if match:
        days = int(match.group(1))
        if days < 1 or days > 31:
            raise TimeParseError(f"Invalid day interval: {days}. Must be 1-31.")
        # Run at 9am every N days
        cron_expr = f"0 9 */{days} * *"
        # Extract time if specified
        time_match = re.search(r'at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', expr_lower)
        if time_match:
            cron_expr = _adjust_cron_time(cron_expr, time_match.group(1), user_tz)
        next_exec = calculate_next_execution(cron_expr, user_tz)
        return (cron_expr, next_exec)

    # Every weekday at TIME
    match = re.search(r'every\s+weekday\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)', expr_lower)
    if match:
        cron_expr = _adjust_cron_time(CRON_PRESETS["weekdays"], match.group(1), user_tz)
        next_exec = calculate_next_execution(cron_expr, user_tz)
        return (cron_expr, next_exec)

    return None


def _adjust_cron_time(cron_expr: str, time_str: str, user_tz: pytz.BaseTzInfo) -> str:
    """
    Adjust a CRON expression's time component.

    Args:
        cron_expr: Base CRON expression
        time_str: Time string like "3pm", "15:30", "9am"
        user_tz: User's timezone (for storing)

    Returns:
        Updated CRON expression with new time
    """
    # Parse the time
    time_str = time_str.strip().lower()

    # Handle 12-hour format
    is_pm = 'pm' in time_str
    is_am = 'am' in time_str
    time_str = time_str.replace('am', '').replace('pm', '').strip()

    if ':' in time_str:
        hour, minute = map(int, time_str.split(':'))
    else:
        hour = int(time_str)
        minute = 0

    # Convert to 24-hour
    if is_pm and hour != 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0

    # Update CRON expression
    parts = cron_expr.split()
    parts[0] = str(minute)
    parts[1] = str(hour)
    return ' '.join(parts)


def calculate_next_execution(cron_expr: str, user_tz: pytz.BaseTzInfo) -> datetime:
    """
    Calculate the next execution time for a CRON expression.

    Args:
        cron_expr: CRON expression (5 fields)
        user_tz: Timezone for the schedule

    Returns:
        Next execution time in UTC
    """
    now = datetime.now(user_tz)
    cron = croniter(cron_expr, now)
    next_local = cron.get_next(datetime)

    # Ensure timezone awareness and convert to UTC
    if next_local.tzinfo is None:
        next_local = user_tz.localize(next_local)

    return next_local.astimezone(pytz.UTC)


def parse_time_expression(expr: str, user_timezone: str = "UTC") -> ParsedTime:
    """
    Parse a time expression into a structured result.

    Supports:
    - Natural language: "in 2 hours", "tomorrow at 10am", "next Monday 3pm"
    - CRON expressions: "0 10 * * *", "0 9 * * 1-5"
    - Presets: "hourly", "daily", "weekly", "weekdays", "monthly"
    - Recurring natural: "every day at 9am", "every weekday at 3pm"

    Args:
        expr: The time expression to parse
        user_timezone: User's timezone (IANA name)

    Returns:
        ParsedTime with execution details

    Raises:
        TimeParseError: If the expression cannot be parsed
    """
    expr = expr.strip()
    if not expr:
        raise TimeParseError("Empty time expression")

    # Validate timezone
    if not validate_timezone(user_timezone):
        logger.warning(f"Invalid timezone '{user_timezone}', falling back to UTC")
        user_timezone = "UTC"

    user_tz = pytz.timezone(user_timezone)

    # Check if it's a preset keyword
    expr_lower = expr.lower()
    if expr_lower in CRON_PRESETS:
        cron_expr = CRON_PRESETS[expr_lower]
        next_exec = calculate_next_execution(cron_expr, user_tz)
        return ParsedTime(
            next_execution=next_exec,
            cron_expression=cron_expr,
            is_recurring=True,
            original_input=expr,
            timezone=user_timezone,
        )

    # Check if it's a CRON expression
    if _is_cron_expression(expr):
        try:
            # Validate with croniter
            croniter(expr)
            next_exec = calculate_next_execution(expr, user_tz)
            return ParsedTime(
                next_execution=next_exec,
                cron_expression=expr,
                is_recurring=True,
                original_input=expr,
                timezone=user_timezone,
            )
        except (ValueError, KeyError) as e:
            raise TimeParseError(f"Invalid CRON expression: {e}")

    # Check for recurring natural language patterns
    recurring_result = _parse_recurring_natural(expr, user_tz)
    if recurring_result:
        cron_expr, next_exec = recurring_result
        return ParsedTime(
            next_execution=next_exec,
            cron_expression=cron_expr,
            is_recurring=True,
            original_input=expr,
            timezone=user_timezone,
        )

    # Try parsing as one-time natural language
    settings = {
        'TIMEZONE': user_timezone,
        'RETURN_AS_TIMEZONE_AWARE': True,
        'PREFER_DATES_FROM': 'future',
    }

    parsed = dateparser.parse(expr, settings=settings)

    if parsed is None:
        raise TimeParseError(
            f"Could not parse time expression: '{expr}'. "
            "Try formats like 'in 2 hours', 'tomorrow at 10am', 'next Monday', "
            "or CRON expressions like '0 10 * * *'."
        )

    # Ensure it's in the future
    now = datetime.now(user_tz)
    if parsed <= now:
        # If parsed time is in the past, dateparser might have gotten the date wrong
        # Try to be more specific
        raise TimeParseError(
            f"Time '{expr}' appears to be in the past. "
            "Try specifying a future date like 'tomorrow at 10am'."
        )

    # Convert to UTC
    next_exec = parsed.astimezone(pytz.UTC)

    return ParsedTime(
        next_execution=next_exec,
        cron_expression=None,
        is_recurring=False,
        original_input=expr,
        timezone=user_timezone,
    )
