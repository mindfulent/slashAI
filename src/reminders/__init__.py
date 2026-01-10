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
Scheduled Reminders Package

Provides scheduled reminder functionality with CRON support.
"""

from .time_parser import (
    ParsedTime,
    TimeParseError,
    parse_time_expression,
    calculate_next_execution,
    validate_timezone,
    CRON_PRESETS,
)
from .manager import ReminderManager
from .scheduler import ReminderScheduler

__all__ = [
    "ParsedTime",
    "TimeParseError",
    "parse_time_expression",
    "calculate_next_execution",
    "validate_timezone",
    "CRON_PRESETS",
    "ReminderManager",
    "ReminderScheduler",
]
