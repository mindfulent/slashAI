# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""Defensive wrapper around discord.py's typing context manager.

POST /typing can return 429 with error code 40062 ("Service resource is being
rate limited") on a per-channel shared bucket — most often when datacenter
egress IPs are deprioritized or multiple bots type in the same channel.
discord.py raises HTTPException out of __aenter__ on the 5th retry, which
crashes the surrounding handler before the actual response is sent.
"""

import logging
from contextlib import asynccontextmanager

import discord

logger = logging.getLogger(__name__)


@asynccontextmanager
async def safe_typing(channel: discord.abc.Messageable):
    """Like ``channel.typing()`` but never raises on rate-limit / network errors.

    If the typing indicator can't be started, the body still runs without it.
    """
    try:
        async with channel.typing():
            yield
    except discord.HTTPException as e:
        logger.warning(
            "Skipping typing indicator for channel %s: %s",
            getattr(channel, "id", "?"),
            e,
        )
        yield
