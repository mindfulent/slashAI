"""
slashAI Discord Bot

Maintains persistent Discord connection and provides methods for MCP tools.
Can also run standalone as a chatbot powered by Claude Sonnet 4.5.
"""

import asyncio
import os
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from claude_client import ClaudeClient

load_dotenv()


class DiscordBot(commands.Bot):
    """Discord bot with MCP-compatible methods and chatbot functionality."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True

        super().__init__(command_prefix="!", intents=intents)

        self.claude_client: Optional[ClaudeClient] = None
        self._ready_event = asyncio.Event()

    async def setup_hook(self):
        """Called when the bot is starting up."""
        # Initialize Claude client for chatbot functionality
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            self.claude_client = ClaudeClient(api_key)

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

        # Chatbot: respond when mentioned or in DMs
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
                )
                await message.reply(response)
            except Exception as e:
                await message.reply(f"Sorry, I encountered an error: {str(e)}")

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
