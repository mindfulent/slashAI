# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Lightweight Discord client for a single agent persona.
Responds to mentions and DMs only — no slash commands, no MCP tools.
"""

import logging
import os
from typing import Optional

import discord

from agents.persona_loader import PersonaConfig
from claude_client import ClaudeClient

logger = logging.getLogger(__name__)

# Discord message length limit
DISCORD_MAX_LENGTH = 2000


class AgentClient(discord.Client):
    """Lightweight Discord client for a single agent persona."""

    def __init__(self, persona: PersonaConfig, memory_manager=None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.persona = persona
        self.claude = ClaudeClient(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            memory_manager=memory_manager,
            system_prompt=persona.build_system_prompt(),
            agent_id=persona.memory.agent_id,
        )

    async def on_ready(self):
        logger.info(
            f"Agent '{self.persona.display_name}' connected as {self.user} "
            f"(agent_id={self.persona.memory.agent_id})"
        )
        # Set Discord presence from persona config
        activity_type = getattr(
            discord.ActivityType,
            self.persona.discord.activity_type,
            discord.ActivityType.playing,
        )
        activity = discord.Activity(
            type=activity_type,
            name=self.persona.discord.status_text or "",
        )
        await self.change_presence(activity=activity)

        # Clear any stale slash commands from previous bot usage of this token
        tree = discord.app_commands.CommandTree(self)
        tree.clear_commands(guild=None)
        await tree.sync()
        logger.info(f"Cleared stale slash commands for '{self.persona.display_name}'")

    async def on_message(self, message: discord.Message):
        # Ignore own messages and other bots
        if message.author == self.user or message.author.bot:
            return

        # Respond to direct mentions and DMs only
        is_mentioned = self.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        if not is_mentioned and not is_dm:
            return

        logger.info(
            f"[{self.persona.display_name}] Message from {message.author.name}: "
            f"{message.content[:100]}"
        )

        async with message.channel.typing():
            try:
                result = await self.claude.chat(
                    user_id=str(message.author.id),
                    channel_id=str(message.channel.id),
                    content=message.content,
                    channel=message.channel,
                )
                await self._send_response(message.channel, result.text)
            except Exception as e:
                logger.error(
                    f"[{self.persona.display_name}] Chat error: {e}", exc_info=True
                )

    async def _send_response(self, channel, text: str):
        """Send response, chunking if over Discord's 2000 char limit."""
        if len(text) <= DISCORD_MAX_LENGTH:
            await channel.send(text)
            return

        # Chunk on sentence boundaries
        chunks = _chunk_message(text, DISCORD_MAX_LENGTH)
        for chunk in chunks:
            await channel.send(chunk)


def _chunk_message(text: str, max_length: int) -> list[str]:
    """Split text into chunks respecting sentence boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Find last sentence break before limit
        split_at = max_length
        for sep in [". ", "! ", "? ", "\n"]:
            idx = remaining[:max_length].rfind(sep)
            if idx > 0:
                split_at = idx + len(sep)
                break

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks
