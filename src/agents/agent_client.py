# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
# SPDX-License-Identifier: AGPL-3.0-only

"""
Lightweight Discord client for a single agent persona.
Responds to mentions and DMs only — no slash commands.
Supports Discord tool use (send/read/edit messages, list channels, etc.).
"""

import logging
import os
import re
from typing import Optional

import discord

from agents.persona_loader import PersonaConfig
from claude_client import ClaudeClient
from voice.session import VoiceSession

logger = logging.getLogger(__name__)

# Discord message length limit
DISCORD_MAX_LENGTH = 2000


class AgentClient(discord.Client):
    """Lightweight Discord client for a single agent persona."""

    def __init__(self, persona: PersonaConfig, memory_manager=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)

        self.persona = persona
        self.claude = ClaudeClient(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            memory_manager=memory_manager,
            system_prompt=persona.build_system_prompt(),
            agent_id=persona.memory.agent_id,
            bot=self,
            is_agent=True,
        )
        self._voice_session: Optional[VoiceSession] = None

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

        # Check for voice commands
        voice_handled = await self._handle_voice_command(message)
        if voice_handled:
            return

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

    # --- Voice support ---

    _VOICE_JOIN_RE = re.compile(
        r"\b(?:join|enter|hop\s+in(?:to)?|come\s+to)\s+(?:voice|vc|the\s+(?:voice|vc))\b",
        re.IGNORECASE,
    )
    _VOICE_LEAVE_RE = re.compile(
        r"\b(?:leave|exit|disconnect\s+from|get\s+out\s+of)\s+(?:voice|vc|the\s+(?:voice|vc))\b",
        re.IGNORECASE,
    )

    async def _handle_voice_command(self, message: discord.Message) -> bool:
        """Check for voice join/leave commands. Returns True if handled."""
        content = message.content.lower()

        if self._VOICE_JOIN_RE.search(content):
            return await self._handle_voice_join(message)

        if self._VOICE_LEAVE_RE.search(content):
            return await self._handle_voice_leave(message)

        return False

    async def _handle_voice_join(self, message: discord.Message) -> bool:
        """Join the user's voice channel."""
        if not os.getenv("CARTESIA_API_KEY"):
            await message.channel.send("Voice is not configured (missing API key).")
            return True

        if self._voice_session and self._voice_session.is_connected:
            await message.channel.send(
                f"I'm already in {self._voice_session.channel.mention}!"
                if self._voice_session.channel
                else "I'm already in a voice channel!"
            )
            return True

        # Find the user's voice channel
        if not message.guild:
            await message.channel.send("Voice only works in servers, not DMs.")
            return True

        member = message.guild.get_member(message.author.id)
        if not member or not member.voice or not member.voice.channel:
            await message.channel.send(
                "Join a voice channel first, then ask me to join!"
            )
            return True

        voice_channel = member.voice.channel
        try:
            self._voice_session = VoiceSession(self, self.persona, self.claude)
            await self._voice_session.join(voice_channel)
            await message.channel.send(f"Joined {voice_channel.mention}!")
        except Exception as e:
            logger.error(
                f"[{self.persona.display_name}] Voice join error: {e}", exc_info=True
            )
            await message.channel.send(f"Couldn't join voice: {e}")
            self._voice_session = None

        return True

    async def _handle_voice_leave(self, message: discord.Message) -> bool:
        """Leave the current voice channel."""
        if not self._voice_session or not self._voice_session.is_connected:
            await message.channel.send("I'm not in a voice channel.")
            return True

        try:
            await self._voice_session.leave()
            self._voice_session = None
            await message.channel.send("Left the voice channel.")
        except Exception as e:
            logger.error(
                f"[{self.persona.display_name}] Voice leave error: {e}", exc_info=True
            )
            self._voice_session = None

        return True

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Auto-leave voice when the channel becomes empty (no humans)."""
        if not self._voice_session or not self._voice_session.is_connected:
            return

        channel = self._voice_session.channel
        if channel is None:
            return

        # Check if a human left our channel
        if before.channel == channel and after.channel != channel:
            humans = [m for m in channel.members if not m.bot]
            if not humans:
                logger.info(
                    f"[{self.persona.display_name}] No humans left in "
                    f"{channel.name}, auto-leaving"
                )
                await self._voice_session.leave()
                self._voice_session = None

    # --- Discord tool methods (used by ClaudeClient._execute_tool) ---

    async def _send_chunked(
        self, channel: discord.abc.Messageable, content: str, reply_to: discord.Message = None
    ) -> discord.Message:
        """Send a message, splitting into chunks if needed. Returns the last message sent."""
        chunks = _chunk_message(content, DISCORD_MAX_LENGTH)
        last_msg = None
        for i, chunk in enumerate(chunks):
            if i == 0 and reply_to:
                last_msg = await reply_to.reply(chunk)
            else:
                last_msg = await channel.send(chunk)
        return last_msg

    async def send_message(self, channel_id: int, content: str) -> discord.Message:
        """Send a message to a channel."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return await self._send_chunked(channel, content)

    async def edit_message(
        self, channel_id: int, message_id: int, content: str
    ) -> discord.Message:
        """Edit a message."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        if len(content) > DISCORD_MAX_LENGTH:
            content = content[: DISCORD_MAX_LENGTH - 20] + "\n\n[...truncated]"
        return await message.edit(content=content)

    async def read_messages(
        self, channel_id: int, limit: int = 10
    ) -> list[discord.Message]:
        """Read recent messages from a channel."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return [msg async for msg in channel.history(limit=limit)]

    async def list_channels(
        self, guild_id: Optional[int] = None
    ) -> list[discord.TextChannel]:
        """List text channels."""
        channels = []
        if guild_id:
            guild = self.get_guild(guild_id)
            if guild:
                channels = [
                    ch for ch in guild.channels if isinstance(ch, discord.TextChannel)
                ]
        else:
            for guild in self.guilds:
                channels.extend(
                    ch for ch in guild.channels if isinstance(ch, discord.TextChannel)
                )
        return channels

    async def get_channel_info(self, channel_id: int) -> dict:
        """Get channel information."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        info = {
            "id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
        }
        if isinstance(channel, discord.TextChannel):
            info.update({
                "topic": channel.topic or "No topic",
                "guild": channel.guild.name,
                "guild_id": channel.guild.id,
                "category": channel.category.name if channel.category else "None",
                "position": channel.position,
                "nsfw": channel.nsfw,
            })
        return info

    async def get_message_image(
        self, channel_id: int, message_id: int
    ) -> tuple[bytes, str] | None:
        """Fetch an image attachment from a message."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                image_bytes = await attachment.read()
                return (image_bytes, attachment.content_type)
        return None


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
