"""
slashAI Discord Bot

Maintains persistent Discord connection and provides methods for MCP tools.
Can also run standalone as a chatbot powered by Claude Sonnet 4.5.
Integrates with memory system for persistent context.
"""

import asyncio
import os
from typing import Optional

import asyncpg
import discord
from anthropic import AsyncAnthropic
from discord.ext import commands
from dotenv import load_dotenv

from claude_client import ClaudeClient

load_dotenv()

import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slashAI")


class DiscordBot(commands.Bot):
    """Discord bot with MCP-compatible methods and chatbot functionality."""

    def __init__(self, enable_chat: bool = True):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True

        super().__init__(command_prefix="!", intents=intents)

        self.enable_chat = enable_chat  # Disable for MCP-only mode
        self.claude_client: Optional[ClaudeClient] = None
        self.db_pool: Optional[asyncpg.Pool] = None
        self._ready_event = asyncio.Event()

    async def setup_hook(self):
        """Called when the bot is starting up."""
        if not self.enable_chat:
            logger.info("MCP-only mode, skipping Claude client setup")
            return

        api_key = os.getenv("ANTHROPIC_API_KEY")
        database_url = os.getenv("DATABASE_URL")
        memory_enabled = os.getenv("MEMORY_ENABLED", "false").lower() == "true"
        voyage_key = os.getenv("VOYAGE_API_KEY")

        logger.info(f"Setup: ANTHROPIC_API_KEY={'set' if api_key else 'missing'}")
        logger.info(f"Setup: DATABASE_URL={'set' if database_url else 'missing'}")
        logger.info(f"Setup: MEMORY_ENABLED={memory_enabled}")
        logger.info(f"Setup: VOYAGE_API_KEY={'set' if voyage_key else 'missing'}")

        if api_key and database_url and memory_enabled:
            # Initialize memory system
            try:
                from memory import MemoryManager

                self.db_pool = await asyncpg.create_pool(database_url)
                anthropic_client = AsyncAnthropic(api_key=api_key)
                memory_manager = MemoryManager(self.db_pool, anthropic_client)
                self.claude_client = ClaudeClient(
                    api_key, memory_manager=memory_manager
                )
                logger.info("Memory system initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize memory system: {e}", exc_info=True)
                logger.warning("Falling back to v0.9.0 behavior (no memory)")
                if api_key:
                    self.claude_client = ClaudeClient(api_key)
        elif api_key:
            # Fallback: no memory system
            logger.info("Memory system disabled, using basic Claude client")
            self.claude_client = ClaudeClient(api_key)
        else:
            logger.warning("No ANTHROPIC_API_KEY, chatbot disabled")

    async def on_ready(self):
        """Called when the bot has connected to Discord."""
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} guild(s)")
        self._ready_event.set()

    def is_ready(self) -> bool:
        """Check if the bot is ready."""
        return self._ready_event.is_set()

    async def wait_until_ready(self):
        """Wait until the bot is ready."""
        await self._ready_event.wait()

    async def on_message(self, message: discord.Message):
        """Handle incoming messages for chatbot functionality."""
        # Ignore messages from the bot itself
        if message.author == self.user:
            return

        # Process commands first
        await self.process_commands(message)

        # Chatbot: respond when mentioned or in DMs (skip if chat disabled)
        if not self.enable_chat:
            return

        if self.user.mentioned_in(message) or isinstance(
            message.channel, discord.DMChannel
        ):
            await self._handle_chat(message)

    async def _handle_chat(self, message: discord.Message):
        """Generate a Claude response to a message."""
        if self.claude_client is None:
            await message.channel.send(
                "Chatbot functionality is not configured (missing ANTHROPIC_API_KEY)."
            )
            return

        # Remove bot mention from content
        content = message.content.replace(f"<@{self.user.id}>", "").strip()

        if not content:
            return

        async with message.channel.typing():
            try:
                response = await self.claude_client.chat(
                    user_id=str(message.author.id),
                    channel_id=str(message.channel.id),
                    content=content,
                    channel=message.channel,  # Pass channel for memory privacy
                )
                await message.reply(response)
            except Exception as e:
                await message.reply(f"Sorry, I encountered an error: {str(e)}")

    async def close(self):
        """Clean up resources on shutdown."""
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

    # --- MCP Tool Methods ---

    async def send_message(self, channel_id: int, content: str) -> discord.Message:
        """Send a message to a channel. Used by MCP tools."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        return await channel.send(content)

    async def edit_message(
        self, channel_id: int, message_id: int, content: str
    ) -> discord.Message:
        """Edit a message. Used by MCP tools."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        return await message.edit(content=content)

    async def read_messages(
        self, channel_id: int, limit: int = 10
    ) -> list[discord.Message]:
        """Read recent messages from a channel. Used by MCP tools."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        messages = [msg async for msg in channel.history(limit=limit)]
        return messages

    async def list_channels(
        self, guild_id: Optional[int] = None
    ) -> list[discord.TextChannel]:
        """List text channels. Used by MCP tools."""
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
        """Get channel information. Used by MCP tools."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)

        info = {
            "id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
        }

        if isinstance(channel, discord.TextChannel):
            info.update(
                {
                    "topic": channel.topic or "No topic",
                    "guild": channel.guild.name,
                    "guild_id": channel.guild.id,
                    "category": channel.category.name if channel.category else "None",
                    "position": channel.position,
                    "nsfw": channel.nsfw,
                }
            )

        return info


async def main():
    """Run the bot standalone (chatbot mode)."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set")
        print("Please set it in your .env file")
        return

    bot = DiscordBot()
    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
