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
Lightweight analytics tracking for slashAI.

Usage:
    from analytics import track, track_async

    # Synchronous (fire-and-forget, uses background task)
    track("message_received", "message", user_id=123, properties={"channel_type": "dm"})

    # Async (when you need to await completion)
    await track_async("command_used", "command", user_id=123, properties={"command": "memories list"})
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)

# Module-level connection pool (initialized lazily)
_pool: Optional[asyncpg.Pool] = None
_enabled: bool = os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"


async def _get_pool() -> Optional[asyncpg.Pool]:
    """Get or create the connection pool."""
    global _pool
    if _pool is None and _enabled:
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            try:
                _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=3)
            except Exception as e:
                logger.warning(f"Analytics pool creation failed: {e}")
                return None
    return _pool


async def track_async(
    event_name: str,
    event_category: str,
    user_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    guild_id: Optional[int] = None,
    properties: Optional[dict[str, Any]] = None,
) -> bool:
    """
    Track an event asynchronously.

    Args:
        event_name: Specific event identifier (e.g., "message_received")
        event_category: One of: message, memory, command, tool, api, error, system
        user_id: Discord user ID (optional)
        channel_id: Discord channel ID (optional)
        guild_id: Discord guild ID (optional)
        properties: Additional event data as key-value pairs

    Returns:
        True if event was recorded, False otherwise
    """
    if not _enabled:
        return False

    pool = await _get_pool()
    if pool is None:
        return False

    try:
        await pool.execute(
            """
            INSERT INTO analytics_events
                (event_name, event_category, user_id, channel_id, guild_id, properties)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            event_name,
            event_category,
            user_id,
            channel_id,
            guild_id,
            json.dumps(properties or {}),
        )
        return True
    except Exception as e:
        logger.debug(f"Analytics tracking failed: {e}")
        return False


def track(
    event_name: str,
    event_category: str,
    user_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    guild_id: Optional[int] = None,
    properties: Optional[dict[str, Any]] = None,
) -> None:
    """
    Track an event (fire-and-forget).

    Creates a background task to record the event without blocking.
    Safe to call from sync or async contexts.
    """
    if not _enabled:
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            track_async(event_name, event_category, user_id, channel_id, guild_id, properties)
        )
    except RuntimeError:
        # No running loop - skip tracking
        pass


async def shutdown() -> None:
    """Close the connection pool. Call on bot shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
