"""
slashAI Discord Bot

Maintains persistent Discord connection and provides methods for MCP tools.
Can also run standalone as a chatbot powered by Claude Sonnet 4.5.
Integrates with memory system for persistent context.
"""

import asyncio
import os
import re
from typing import Optional

import asyncpg
import discord
from anthropic import AsyncAnthropic
from discord.ext import commands
from dotenv import load_dotenv

from claude_client import ClaudeClient

load_dotenv()

import logging

# Discord message length limit
DISCORD_MAX_LENGTH = 2000

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
        self.image_observer = None  # Image memory system
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
        image_memory_enabled = os.getenv("IMAGE_MEMORY_ENABLED", "false").lower() == "true"

        logger.info(f"Setup: ANTHROPIC_API_KEY={'set' if api_key else 'missing'}")
        logger.info(f"Setup: DATABASE_URL={'set' if database_url else 'missing'}")
        logger.info(f"Setup: MEMORY_ENABLED={memory_enabled}")
        logger.info(f"Setup: VOYAGE_API_KEY={'set' if voyage_key else 'missing'}")
        logger.info(f"Setup: IMAGE_MEMORY_ENABLED={image_memory_enabled}")

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

                # Initialize image memory if enabled
                if image_memory_enabled and self._has_image_memory_config():
                    await self._setup_image_memory(anthropic_client)

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

    def _has_image_memory_config(self) -> bool:
        """Check if required image memory configuration is present."""
        spaces_key = os.getenv("DO_SPACES_KEY")
        spaces_secret = os.getenv("DO_SPACES_SECRET")
        return bool(spaces_key and spaces_secret)

    async def _setup_image_memory(self, anthropic_client: AsyncAnthropic):
        """Initialize the image memory system."""
        try:
            from memory.images import ImageObserver, ImageStorage

            storage = ImageStorage()
            self.image_observer = ImageObserver(
                db_pool=self.db_pool,
                anthropic_client=anthropic_client,
                storage=storage,
                moderation_enabled=os.getenv("IMAGE_MODERATION_ENABLED", "true").lower() == "true",
            )
            logger.info("Image memory system initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize image memory: {e}", exc_info=True)
            logger.warning("Image memory disabled due to initialization failure")
            self.image_observer = None

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

        # Process image attachments for memory (before chat handling)
        if message.attachments:
            logger.info(f"[IMAGE] Message has {len(message.attachments)} attachments, image_observer={'enabled' if self.image_observer else 'DISABLED'}")
        if self.image_observer and message.attachments:
            await self._process_image_attachments(message)

        # Chatbot: respond when mentioned or in DMs (skip if chat disabled)
        if not self.enable_chat:
            return

        if self.user.mentioned_in(message) or isinstance(
            message.channel, discord.DMChannel
        ):
            await self._handle_chat(message)

    async def _process_image_attachments(self, message: discord.Message):
        """Process image attachments for memory system."""
        logger.info(f"[IMAGE] Processing {len(message.attachments)} attachment(s) from user {message.author.id}")
        
        for i, attachment in enumerate(message.attachments):
            logger.info(f"[IMAGE] Attachment {i+1}: filename={attachment.filename}, size={attachment.size}, content_type={attachment.content_type}, url={attachment.url[:80]}...")
            
            # Check if it is a supported image format
            if self._is_supported_image(attachment.filename):
                logger.info(f"[IMAGE] Supported format detected, starting processing...")
                try:
                    observation_id = await self.image_observer.handle_image(
                        message, attachment, bot=self
                    )
                    if observation_id:
                        logger.info(
                            f"[IMAGE] SUCCESS: Stored observation {observation_id} for user {message.author.id}"
                        )
                    else:
                        logger.warning(f"[IMAGE] handle_image returned None (rejected/moderated/duplicate)")
                except Exception as e:
                    logger.error(
                        f"[IMAGE] FAILED to process image from {message.author.id}: {e}",
                        exc_info=True,
                    )
            else:
                logger.debug(f"[IMAGE] Skipping unsupported format: {attachment.filename}")

    def _is_supported_image(self, filename: str) -> bool:
        """Check if file extension is a supported image format."""
        if not filename:
            return False
        ext = filename.rsplit(".", 1)[-1].lower()
        return ext in {"png", "jpg", "jpeg", "gif", "webp"}

    async def _handle_chat(self, message: discord.Message):
        """Generate a Claude response to a message."""
        if self.claude_client is None:
            await message.channel.send(
                "Chatbot functionality is not configured (missing ANTHROPIC_API_KEY)."
            )
            return

        # Remove bot mention from content
        content = message.content.replace(f"<@{self.user.id}>", "").strip()

        # Process text file attachments (.md, .txt, .py, .json, etc.)
        attachment_contents = await self._read_text_attachments(message.attachments)
        if attachment_contents:
            content = f"{content}\n\n{attachment_contents}" if content else attachment_contents

        # Download image attachments for vision
        images = await self._read_image_attachments(message.attachments)

        # Need either text or images to proceed
        if not content and not images:
            return

        async with message.channel.typing():
            try:
                response = await self.claude_client.chat(
                    user_id=str(message.author.id),
                    channel_id=str(message.channel.id),
                    content=content,
                    channel=message.channel,  # Pass channel for memory privacy
                    images=images if images else None,
                )
                await self._send_chunked(message.channel, response, reply_to=message)
            except Exception as e:
                logger.error(f"Chat error: {e}", exc_info=True)
                await message.reply(f"Sorry, I encountered an error: {str(e)}")

    async def _read_text_attachments(
        self, attachments: list[discord.Attachment]
    ) -> str:
        """Download and read text-based file attachments."""
        TEXT_EXTENSIONS = {
            ".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
            ".toml", ".ini", ".cfg", ".conf", ".sh", ".bash", ".zsh",
            ".html", ".css", ".xml", ".csv", ".log", ".sql", ".r", ".rs",
            ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb",
        }
        MAX_FILE_SIZE = 100_000  # 100KB limit per file

        parts = []
        for attachment in attachments:
            # Check file extension
            ext = "." + attachment.filename.lower().rsplit(".", 1)[-1] if "." in attachment.filename else ""
            if ext not in TEXT_EXTENSIONS:
                continue

            # Check file size
            if attachment.size > MAX_FILE_SIZE:
                parts.append(f"[File {attachment.filename} too large ({attachment.size} bytes)]")
                continue

            try:
                content_bytes = await attachment.read()
                content = content_bytes.decode("utf-8", errors="replace")
                logger.info(f"Read attachment: {attachment.filename} ({len(content)} chars)")
                # Use XML tags to avoid conflicts with code blocks inside the file
                parts.append(f'<attached_file name="{attachment.filename}">\n{content}\n</attached_file>')
            except Exception as e:
                logger.warning(f"Failed to read attachment {attachment.filename}: {e}")
                parts.append(f"[Failed to read {attachment.filename}: {e}]")

        if not parts and attachments:
            logger.debug(f"No text attachments in {len(attachments)} file(s)")

        return "\n\n".join(parts)

    async def _read_image_attachments(
        self, attachments: list[discord.Attachment]
    ) -> list[tuple[bytes, str]]:
        """Download image attachments for vision analysis.

        Returns:
            List of (image_bytes, media_type) tuples
        """
        IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
        MAX_IMAGE_SIZE = 20_000_000  # 20MB limit

        images = []
        for attachment in attachments:
            # Check file extension
            ext = attachment.filename.lower().rsplit(".", 1)[-1] if "." in attachment.filename else ""
            if ext not in IMAGE_EXTENSIONS:
                continue

            # Check file size
            if attachment.size > MAX_IMAGE_SIZE:
                logger.warning(f"Image too large for vision: {attachment.filename} ({attachment.size} bytes)")
                continue

            try:
                image_bytes = await attachment.read()
                # Map extension to media type
                media_type = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "webp": "image/webp",
                }.get(ext, "image/png")
                images.append((image_bytes, media_type))
                logger.info(f"Read image for vision: {attachment.filename} ({len(image_bytes)} bytes)")
            except Exception as e:
                logger.warning(f"Failed to read image {attachment.filename}: {e}")

        return images

    def _chunk_message(self, content: str) -> list[str]:
        """Split a message into chunks that fit Discord's 2000 char limit.

        Uses semantic chunking for markdown: prefers splitting on headers (##, ###, etc.)
        before falling back to paragraph breaks, then sentence breaks, then word breaks.
        """
        if len(content) <= DISCORD_MAX_LENGTH:
            return [content]

        # Try semantic chunking first for markdown content
        if re.search(r'^#{1,6}\s', content, re.MULTILINE):
            chunks = self._chunk_by_headers(content)
            if chunks:
                return chunks

        # Fallback to simple chunking
        return self._chunk_simple(content)

    def _chunk_by_headers(self, content: str) -> list[str]:
        """Split content by markdown headers, keeping structure intact."""
        # Split on headers (keep the header with its section)
        header_pattern = r'(?=^#{1,6}\s)'
        sections = re.split(header_pattern, content, flags=re.MULTILINE)
        sections = [s for s in sections if s.strip()]

        chunks = []
        current_chunk = ""

        for section in sections:
            # If adding this section would exceed limit
            if len(current_chunk) + len(section) > DISCORD_MAX_LENGTH:
                # Save current chunk if non-empty
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())

                # If section itself exceeds limit, sub-chunk it
                if len(section) > DISCORD_MAX_LENGTH:
                    sub_chunks = self._chunk_simple(section)
                    chunks.extend(sub_chunks)
                    current_chunk = ""
                else:
                    current_chunk = section
            else:
                current_chunk += section

        # Don't forget the last chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _chunk_simple(self, content: str) -> list[str]:
        """Simple chunking: split at paragraph, sentence, or word boundaries."""
        chunks = []
        remaining = content

        while remaining:
            if len(remaining) <= DISCORD_MAX_LENGTH:
                chunks.append(remaining)
                break

            # Find best break point (prefer paragraph > sentence > word)
            break_at = DISCORD_MAX_LENGTH

            # Try paragraph break (double newline)
            para_idx = remaining.rfind("\n\n", 0, DISCORD_MAX_LENGTH)
            if para_idx > DISCORD_MAX_LENGTH // 2:
                break_at = para_idx + 2
            else:
                # Try single newline
                newline_idx = remaining.rfind("\n", 0, DISCORD_MAX_LENGTH)
                if newline_idx > DISCORD_MAX_LENGTH // 2:
                    break_at = newline_idx + 1
                else:
                    # Try sentence end (. ! ?)
                    for punct in [". ", "! ", "? "]:
                        punct_idx = remaining.rfind(punct, 0, DISCORD_MAX_LENGTH)
                        if punct_idx > DISCORD_MAX_LENGTH // 2:
                            break_at = punct_idx + len(punct)
                            break
                    else:
                        # Last resort: word break
                        space_idx = remaining.rfind(" ", 0, DISCORD_MAX_LENGTH)
                        if space_idx > DISCORD_MAX_LENGTH // 2:
                            break_at = space_idx + 1

            chunks.append(remaining[:break_at].rstrip())
            remaining = remaining[break_at:].lstrip()

        return chunks

    async def _send_chunked(
        self, channel: discord.abc.Messageable, content: str, reply_to: discord.Message = None
    ) -> discord.Message:
        """Send a message, splitting into chunks if needed. Returns the last message sent."""
        chunks = self._chunk_message(content)
        last_msg = None

        for i, chunk in enumerate(chunks):
            if i == 0 and reply_to:
                last_msg = await reply_to.reply(chunk)
            else:
                last_msg = await channel.send(chunk)

        return last_msg

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
        return await self._send_chunked(channel, content)

    async def edit_message(
        self, channel_id: int, message_id: int, content: str
    ) -> discord.Message:
        """Edit a message. Used by MCP tools."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        # Truncate if too long (edits can't be split)
        if len(content) > DISCORD_MAX_LENGTH:
            content = content[: DISCORD_MAX_LENGTH - 20] + "\n\n[...truncated]"
        return await message.edit(content=content)

    async def delete_message(self, channel_id: int, message_id: int) -> None:
        """Delete a message. Used by MCP tools."""
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        await message.delete()

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
