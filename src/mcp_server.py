"""
slashAI MCP Server

Exposes Discord operations as MCP tools for Claude Code.
Uses FastMCP for decorator-based tool definitions.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from discord_bot import DiscordBot

load_dotenv()

# Discord bot instance (initialized on startup)
bot: Optional[DiscordBot] = None


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Start Discord bot when MCP server starts."""
    global bot
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN environment variable not set")

    bot = DiscordBot()
    bot_task = asyncio.create_task(bot.start(token))

    # Wait for bot to be ready
    try:
        await asyncio.wait_for(bot._ready_event.wait(), timeout=30.0)
        print(f"Discord bot connected as {bot.user}", flush=True)
    except asyncio.TimeoutError:
        raise RuntimeError("Discord bot failed to connect within 30 seconds")

    yield  # Server runs here

    # Cleanup on shutdown
    await bot.close()
    bot_task.cancel()


# Initialize MCP server with lifespan
mcp = FastMCP(name="slashAI", lifespan=lifespan)


@mcp.tool()
async def send_message(channel_id: str, content: str) -> str:
    """
    Send a message to a Discord channel.

    Args:
        channel_id: The Discord channel ID to send the message to
        content: The message content to send

    Returns:
        Confirmation with the sent message ID
    """
    if bot is None:
        return "Error: Discord bot not initialized"

    try:
        message = await bot.send_message(int(channel_id), content)
        return f"Message sent successfully. Message ID: {message.id}"
    except Exception as e:
        return f"Error sending message: {str(e)}"


@mcp.tool()
async def edit_message(channel_id: str, message_id: str, content: str) -> str:
    """
    Edit an existing message in a Discord channel.

    Args:
        channel_id: The Discord channel ID containing the message
        message_id: The ID of the message to edit
        content: The new content for the message

    Returns:
        Confirmation of the edit
    """
    if bot is None:
        return "Error: Discord bot not initialized"

    try:
        await bot.edit_message(int(channel_id), int(message_id), content)
        return f"Message {message_id} edited successfully"
    except Exception as e:
        return f"Error editing message: {str(e)}"


@mcp.tool()
async def read_messages(channel_id: str, limit: int = 10) -> str:
    """
    Read recent messages from a Discord channel.

    Args:
        channel_id: The Discord channel ID to read from
        limit: Maximum number of messages to fetch (default 10, max 100)

    Returns:
        Formatted list of recent messages with author and content
    """
    if bot is None:
        return "Error: Discord bot not initialized"

    try:
        limit = min(limit, 100)  # Cap at 100
        messages = await bot.read_messages(int(channel_id), limit)

        if not messages:
            return "No messages found in this channel"

        formatted = []
        for msg in messages:
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            formatted.append(f"[{timestamp}] {msg.author.name}: {msg.content}")

        return "\n".join(formatted)
    except Exception as e:
        return f"Error reading messages: {str(e)}"


@mcp.tool()
async def list_channels(guild_id: Optional[str] = None) -> str:
    """
    List all text channels the bot has access to.

    Args:
        guild_id: Optional guild ID to filter channels (lists all if not provided)

    Returns:
        List of channels with their IDs and names
    """
    if bot is None:
        return "Error: Discord bot not initialized"

    try:
        channels = await bot.list_channels(int(guild_id) if guild_id else None)

        if not channels:
            return "No channels found"

        formatted = []
        for channel in channels:
            guild_name = channel.guild.name if channel.guild else "Unknown"
            formatted.append(f"[{channel.id}] #{channel.name} (in {guild_name})")

        return "\n".join(formatted)
    except Exception as e:
        return f"Error listing channels: {str(e)}"


@mcp.tool()
async def get_channel_info(channel_id: str) -> str:
    """
    Get detailed information about a Discord channel.

    Args:
        channel_id: The Discord channel ID

    Returns:
        Channel details including name, topic, guild, and member count
    """
    if bot is None:
        return "Error: Discord bot not initialized"

    try:
        info = await bot.get_channel_info(int(channel_id))
        return "\n".join(f"{k}: {v}" for k, v in info.items())
    except Exception as e:
        return f"Error getting channel info: {str(e)}"


if __name__ == "__main__":
    # Run MCP server with stdio transport
    mcp.run(transport="stdio")
