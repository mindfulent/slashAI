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
slashAI Discord Bot

Maintains persistent Discord connection and provides methods for MCP tools.
Can also run standalone as a chatbot powered by Claude Sonnet 4.5.
Integrates with memory system for persistent context.
"""

import asyncio
import io
import json
import os
import re
import time
from typing import Optional

import asyncpg
import discord
from aiohttp import web
from anthropic import AsyncAnthropic
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image

from analytics import track, shutdown as analytics_shutdown
from claude_client import ClaudeClient

load_dotenv()

import logging

# Discord message length limit
DISCORD_MAX_LENGTH = 2000

# Anthropic API limits for images
MAX_IMAGE_BYTES = 1_000_000  # ~1MB limit (accounts for base64 overhead + API efficiency)
MAX_IMAGE_DIMENSION = 2048  # Max 2048px (Anthropic downsamples to ~1.15MP anyway)


def normalize_image_for_api(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """
    Normalize an image to ensure API compatibility.

    Fixes common issues that cause "Could not process image" errors:
    - CMYK/YCCK color space (convert to RGB)
    - Progressive JPEG encoding
    - Problematic EXIF metadata
    - Palette mode images

    Args:
        image_bytes: Original image data
        media_type: MIME type (e.g., "image/jpeg")

    Returns:
        Tuple of (normalized_bytes, media_type)
    """
    img = None
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Check if normalization is needed
        needs_normalize = (
            img.mode in ("CMYK", "YCCK", "LAB", "P", "PA", "LA", "I", "F") or
            media_type == "image/jpeg"  # Always re-encode JPEGs to strip EXIF/fix progressive
        )

        if not needs_normalize:
            return image_bytes, media_type

        # Convert to RGB (or RGBA if has transparency)
        if img.mode in ("RGBA", "LA", "PA"):
            img = img.convert("RGBA")
            out_format = "PNG"
            out_media = "image/png"
        elif img.mode in ("CMYK", "YCCK", "LAB", "I", "F"):
            img = img.convert("RGB")
            out_format = "JPEG"
            out_media = "image/jpeg"
        elif img.mode == "P":
            # Palette mode - check if has transparency
            if img.info.get("transparency") is not None:
                img = img.convert("RGBA")
                out_format = "PNG"
                out_media = "image/png"
            else:
                img = img.convert("RGB")
                out_format = "JPEG"
                out_media = "image/jpeg"
        else:
            # RGB or L mode - just re-encode
            if img.mode == "L":
                img = img.convert("RGB")
            out_format = "JPEG"
            out_media = "image/jpeg"

        # Re-encode (strips EXIF, fixes progressive JPEG, normalizes encoding)
        buffer = io.BytesIO()
        if out_format == "JPEG":
            img.save(buffer, format="JPEG", quality=90, optimize=True)
        else:
            img.save(buffer, format="PNG", optimize=True)

        result = buffer.getvalue()
        logger.info(f"[NORMALIZE] Converted {media_type} {img.mode} -> {out_media} ({len(image_bytes)} -> {len(result)} bytes)")
        return result, out_media

    except Exception as e:
        logger.warning(f"[NORMALIZE] Failed to normalize image: {e}")
        return image_bytes, media_type
    finally:
        if img is not None:
            img.close()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("slashAI")


def resize_image_for_api(image_bytes: bytes, media_type: str, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """
    Resize an image for optimal API transmission.

    Always resizes large images first (Anthropic downsamples to ~1.15MP anyway),
    then compresses to stay under the byte limit.

    Args:
        image_bytes: Original image data
        media_type: MIME type (e.g., "image/jpeg")
        max_bytes: Maximum allowed size in bytes

    Returns:
        Tuple of (resized_bytes, media_type) - media_type may change to JPEG for better compression
    """
    img = None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        original_size = img.size
        needs_resize = img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION

        # Always resize if dimensions exceed limit (no point sending 4K to API)
        if needs_resize:
            ratio = min(MAX_IMAGE_DIMENSION / img.width, MAX_IMAGE_DIMENSION / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            logger.info(f"[RESIZE] Reduced dimensions from {original_size} to {new_size}")

        # Convert to RGB for JPEG compression (no alpha channel)
        save_img = img
        if img.mode in ("RGBA", "P", "PA", "LA"):
            rgb_img = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                save_img = img.convert("RGBA")
            elif img.mode in ("PA", "LA"):
                save_img = img.convert("RGBA")
            else:
                save_img = img
            if save_img.mode == "RGBA":
                rgb_img.paste(save_img, mask=save_img.split()[3])
            else:
                rgb_img.paste(save_img)
            save_img = rgb_img
        elif img.mode == "L":
            save_img = img.convert("RGB")

        # Try progressively lower quality until under limit
        result_bytes = image_bytes  # fallback
        for quality in [85, 70, 55, 40]:
            buffer = io.BytesIO()
            save_img.save(buffer, format="JPEG", quality=quality, optimize=True)
            result_bytes = buffer.getvalue()

            if len(result_bytes) <= max_bytes:
                if needs_resize or len(image_bytes) > max_bytes:
                    logger.info(f"[RESIZE] Compressed to {len(result_bytes)} bytes at quality={quality}")
                return result_bytes, "image/jpeg"

        # Last resort: reduce dimensions further
        while len(result_bytes) > max_bytes and min(img.size) > 100:
            new_size = (img.width // 2, img.height // 2)
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            # Reconvert to RGB after resize
            if img.mode in ("RGBA", "P", "PA", "LA"):
                rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                rgba_img = img.convert("RGBA") if img.mode != "RGBA" else img
                rgb_img.paste(rgba_img, mask=rgba_img.split()[3])
                save_img = rgb_img
            else:
                save_img = img if img.mode == "RGB" else img.convert("RGB")
            buffer = io.BytesIO()
            save_img.save(buffer, format="JPEG", quality=40, optimize=True)
            result_bytes = buffer.getvalue()
            logger.info(f"[RESIZE] Further reduced to {new_size}, now {len(result_bytes)} bytes")

        return result_bytes, "image/jpeg"

    except Exception as e:
        logger.error(f"[RESIZE] Failed to resize image: {e}")
        return image_bytes, media_type  # Return original on failure
    finally:
        if img is not None:
            img.close()


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
        self.reminder_manager = None  # Reminder system (v0.9.17)
        self.reminder_scheduler = None  # Background scheduler for reminders
        self.decay_job = None  # Memory decay job (v0.10.1)
        self.recognition_scheduler = None  # Recognition system for build reviews
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
        owner_id = os.getenv("OWNER_ID")  # Discord user ID for agentic tools

        logger.info(f"Setup: ANTHROPIC_API_KEY={'set' if api_key else 'missing'}")
        logger.info(f"Setup: DATABASE_URL={'set' if database_url else 'missing'}")
        logger.info(f"Setup: MEMORY_ENABLED={memory_enabled}")
        logger.info(f"Setup: VOYAGE_API_KEY={'set' if voyage_key else 'missing'}")
        logger.info(f"Setup: IMAGE_MEMORY_ENABLED={image_memory_enabled}")
        logger.info(f"Setup: OWNER_ID={'set' if owner_id else 'not set (tools disabled)'}")

        if api_key and database_url and memory_enabled:
            # Initialize memory system
            try:
                from memory import MemoryManager

                self.db_pool = await asyncpg.create_pool(
                    database_url, min_size=2, max_size=5
                )
                anthropic_client = AsyncAnthropic(api_key=api_key)
                memory_manager = MemoryManager(self.db_pool, anthropic_client)
                self.claude_client = ClaudeClient(
                    api_key,
                    memory_manager=memory_manager,
                    bot=self,
                    owner_id=owner_id,
                )
                logger.info("Memory system initialized successfully")

                # Load memory management slash commands (v0.9.11)
                try:
                    from commands.memory_commands import MemoryCommands
                    await self.add_cog(MemoryCommands(self, self.db_pool, memory_manager))
                    logger.info("Memory commands cog loaded")
                except Exception as e:
                    logger.error(f"Failed to load memory commands: {e}", exc_info=True)

                # Load analytics slash commands (owner-only)
                try:
                    from commands.analytics_commands import AnalyticsCommands
                    await self.add_cog(AnalyticsCommands(self, self.db_pool))
                    logger.info("Analytics commands cog loaded")
                except Exception as e:
                    logger.error(f"Failed to load analytics commands: {e}", exc_info=True)

                # Load StreamCraft slash commands (owner-only)
                try:
                    from commands.streamcraft_commands import StreamCraftCommands
                    await self.add_cog(StreamCraftCommands(self, self.db_pool))
                    logger.info("StreamCraft commands cog loaded")
                except Exception as e:
                    logger.error(f"Failed to load StreamCraft commands: {e}", exc_info=True)

                # Load Discord account linking commands (CoreCurriculum)
                try:
                    from commands.link_commands import LinkCommands
                    await self.add_cog(LinkCommands(self))
                    logger.info("Link commands cog loaded")
                except Exception as e:
                    logger.error(f"Failed to load link commands: {e}", exc_info=True)

                # Initialize reminder system (v0.9.17)
                try:
                    from reminders import ReminderManager, ReminderScheduler
                    from commands.reminder_commands import ReminderCommands

                    self.reminder_manager = ReminderManager(self.db_pool)
                    self.reminder_scheduler = ReminderScheduler(self, self.db_pool)

                    await self.add_cog(ReminderCommands(
                        self, self.db_pool, self.reminder_manager, owner_id
                    ))
                    logger.info("Reminder system initialized successfully")
                except Exception as e:
                    logger.error(f"Failed to initialize reminder system: {e}", exc_info=True)
                    logger.warning("Reminders disabled due to initialization failure")

                # Initialize memory decay job (v0.10.1)
                try:
                    from memory.decay import MemoryDecayJob

                    self.decay_job = MemoryDecayJob(self.db_pool)
                    self.decay_job.start()
                except Exception as e:
                    logger.error(f"Failed to initialize decay job: {e}", exc_info=True)
                    logger.warning("Memory decay disabled due to initialization failure")

                # Initialize recognition scheduler for Core Curriculum
                recognition_enabled = os.getenv("RECOGNITION_ENABLED", "false").lower() == "true"
                if recognition_enabled:
                    try:
                        from recognition import RecognitionScheduler

                        self.recognition_scheduler = RecognitionScheduler(self)
                        logger.info("Recognition scheduler initialized")
                    except Exception as e:
                        logger.error(f"Failed to initialize recognition scheduler: {e}", exc_info=True)
                        logger.warning("Recognition processing disabled due to initialization failure")

                # Initialize image memory if enabled
                if image_memory_enabled and self._has_image_memory_config():
                    await self._setup_image_memory(anthropic_client)

            except Exception as e:
                logger.error(f"Failed to initialize memory system: {e}", exc_info=True)
                logger.warning("Falling back to v0.9.0 behavior (no memory)")
                if api_key:
                    self.claude_client = ClaudeClient(
                        api_key, bot=self, owner_id=owner_id
                    )
        elif api_key:
            # Fallback: no memory system
            logger.info("Memory system disabled, using basic Claude client")
            self.claude_client = ClaudeClient(api_key, bot=self, owner_id=owner_id)
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

        # Sync slash commands to Discord (v0.9.11)
        # Skip in MCP-only mode to avoid wiping commands registered by the main bot
        if self.enable_chat:
            try:
                synced = await self.tree.sync()
                logger.info(f"Synced {len(synced)} slash command(s)")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}", exc_info=True)

            # Start reminder scheduler (v0.9.17)
            if self.reminder_scheduler:
                self.reminder_scheduler.start()

            # Start recognition scheduler for Core Curriculum
            if self.recognition_scheduler:
                self.recognition_scheduler.start()
        else:
            logger.info("MCP-only mode, skipping command sync")

        self._ready_event.set()

    def is_ready(self) -> bool:
        """Check if the bot is ready."""
        return self._ready_event.is_set()

    async def wait_until_ready(self):
        """Wait until the bot is ready."""
        await self._ready_event.wait()

    async def on_message(self, message: discord.Message):
        """Handle incoming messages for chatbot functionality."""
        # Ignore messages from bots (including self)
        if message.author.bot:
            return

        # DEBUG: Log every incoming message to diagnose mobile upload issues
        has_attachments = len(message.attachments) > 0
        has_embeds = len(message.embeds) > 0
        # Check for direct mention only (ignore @everyone/@here)
        is_mention = self.user in message.mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        logger.info(
            f"[MSG] from={message.author.name} channel={getattr(message.channel, 'name', 'DM')} "
            f"attachments={len(message.attachments)} embeds={len(message.embeds)} "
            f"mention={is_mention} dm={is_dm} content_len={len(message.content)}"
        )

        # Analytics: Track message received
        track(
            "message_received",
            "message",
            user_id=message.author.id,
            channel_id=message.channel.id,
            guild_id=message.guild.id if message.guild else None,
            properties={
                "channel_type": "dm" if is_dm else "guild",
                "has_attachments": has_attachments,
                "content_length": len(message.content),
                "is_mention": is_mention,
            },
        )

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

        # Respond to direct mentions (@slashAI) or DMs, but NOT @everyone/@here
        if self.user in message.mentions or isinstance(
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

        start_time = time.time()
        async with message.channel.typing():
            try:
                response = await self.claude_client.chat(
                    user_id=str(message.author.id),
                    channel_id=str(message.channel.id),
                    content=content,
                    channel=message.channel,  # Pass channel for memory privacy
                    images=images if images else None,
                )
                chunks = self._chunk_message(response)
                await self._send_chunked(message.channel, response, reply_to=message)

                # Analytics: Track response sent
                latency_ms = int((time.time() - start_time) * 1000)
                track(
                    "response_sent",
                    "message",
                    user_id=message.author.id,
                    channel_id=message.channel.id,
                    guild_id=message.guild.id if message.guild else None,
                    properties={
                        "response_length": len(response),
                        "chunk_count": len(chunks),
                        "latency_ms": latency_ms,
                        "has_images": bool(images),
                    },
                )
            except Exception as e:
                logger.error(f"Chat error: {e}", exc_info=True)
                # Analytics: Track error
                track(
                    "chat_error",
                    "error",
                    user_id=message.author.id,
                    channel_id=message.channel.id,
                    guild_id=message.guild.id if message.guild else None,
                    properties={
                        "error_type": type(e).__name__,
                        "error_message": str(e)[:200],
                    },
                )
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
        MAX_IMAGE_SIZE = 20_000_000  # 20MB limit for downloading

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

                logger.info(f"Read image for vision: {attachment.filename} ({len(image_bytes)} bytes)")

                # Normalize first (fix color space, strip EXIF, re-encode)
                image_bytes, media_type = normalize_image_for_api(image_bytes, media_type)

                # Then resize if too large for Anthropic API (5MB limit)
                image_bytes, media_type = resize_image_for_api(image_bytes, media_type)

                images.append((image_bytes, media_type))
            except Exception as e:
                logger.warning(f"Failed to read image {attachment.filename}: {e}", exc_info=True)

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
        # Stop reminder scheduler (v0.9.17)
        if self.reminder_scheduler:
            self.reminder_scheduler.stop()
        # Stop decay job (v0.10.1)
        if self.decay_job:
            self.decay_job.stop()
        # Stop recognition scheduler
        if self.recognition_scheduler:
            await self.recognition_scheduler.close()
        await analytics_shutdown()
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

    # --- MCP Tool Methods ---

    def resolve_channel(self, channel_ref: str) -> Optional[discord.TextChannel]:
        """
        Resolve a channel reference (ID or name) to a TextChannel.

        Args:
            channel_ref: Channel ID (numeric string) or name (e.g., "server-general")

        Returns:
            TextChannel if found, None otherwise

        Matching priority:
        1. Exact numeric ID
        2. Exact channel name match (case-insensitive)
        3. Partial name match - channel name contains the search term
        4. Fuzzy match - search term found after stripping emoji prefixes
        """
        # Try numeric ID first
        try:
            channel_id = int(channel_ref)
            channel = self.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                return channel
        except ValueError:
            pass  # Not a numeric ID, try name matching

        # Normalize the search term
        search = channel_ref.lower().strip().lstrip("#")

        # Collect all text channels
        all_channels = []
        for guild in self.guilds:
            for ch in guild.channels:
                if isinstance(ch, discord.TextChannel):
                    all_channels.append(ch)

        # Try exact name match first
        for ch in all_channels:
            if ch.name.lower() == search:
                return ch

        # Try partial match (search term in channel name)
        for ch in all_channels:
            if search in ch.name.lower():
                return ch

        # Try fuzzy match - strip emoji prefixes from channel names
        # Common pattern: "🖥️server-general" or "❗server-releases"
        for ch in all_channels:
            # Strip leading non-ascii characters (emojis)
            stripped_name = "".join(c for c in ch.name if c.isascii()).lower().strip()
            if stripped_name == search or search in stripped_name:
                return ch

        return None

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

    async def get_message_image(
        self, channel_id: int, message_id: int
    ) -> tuple[bytes, str] | None:
        """
        Fetch an image attachment from a specific message.

        Args:
            channel_id: The channel containing the message
            message_id: The message ID to fetch

        Returns:
            Tuple of (image_bytes, media_type) or None if no image found
        """
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)

        message = await channel.fetch_message(message_id)

        # Find first image attachment
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                image_bytes = await attachment.read()
                return (image_bytes, attachment.content_type)

        return None

    async def search_messages(
        self,
        query: str,
        channel_id: Optional[int] = None,
        author: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Search messages by content, optionally filtering by channel and/or author.

        Args:
            query: Text to search for (case-insensitive)
            channel_id: Optional channel to search (if None, searches all channels)
            author: Optional username/display name to filter by
            limit: Maximum results to return

        Returns:
            List of message dicts with id, author info, content, timestamp
        """
        # Determine which channels to search
        if channel_id is not None:
            channel = self.get_channel(channel_id)
            if channel is None:
                channel = await self.fetch_channel(channel_id)
            channels_to_search = [channel]
        else:
            # Search all accessible text channels
            channels_to_search = []
            for guild in self.guilds:
                for ch in guild.channels:
                    if isinstance(ch, discord.TextChannel):
                        channels_to_search.append(ch)

        # Resolve author username to ID if provided (check first guild)
        author_id = None
        if author and channels_to_search:
            first_channel = channels_to_search[0]
            if hasattr(first_channel, "guild"):
                # Try exact match first (username or display name)
                member = first_channel.guild.get_member_named(author)
                if member:
                    author_id = member.id
                else:
                    # Try case-insensitive partial match
                    author_lower = author.lower()
                    for m in first_channel.guild.members:
                        if (
                            author_lower in m.name.lower()
                            or author_lower in m.display_name.lower()
                        ):
                            author_id = m.id
                            break

        # Search through channels
        results = []
        query_lower = query.lower()

        # Limit messages per channel when doing cross-channel search
        if channel_id is None:
            per_channel_limit = max(50, 200 // len(channels_to_search)) if channels_to_search else 50
        else:
            per_channel_limit = min(limit * 20, 500)

        for channel in channels_to_search:
            try:
                async for msg in channel.history(limit=per_channel_limit):
                    # Filter by author if specified
                    if author_id and msg.author.id != author_id:
                        continue

                    # Filter by content (case-insensitive substring match)
                    if query_lower not in msg.content.lower():
                        continue

                    results.append(
                        {
                            "message_id": str(msg.id),
                            "author_id": str(msg.author.id),
                            "author_name": msg.author.name,
                            "author_display_name": msg.author.display_name,
                            "content": msg.content[:500] if len(msg.content) > 500 else msg.content,
                            "timestamp": msg.created_at.isoformat(),
                            "channel_id": str(channel.id),
                            "channel_name": getattr(channel, "name", "DM"),
                        }
                    )

                    # Early exit if we have enough results for single-channel search
                    if channel_id is not None and len(results) >= limit:
                        break
            except discord.Forbidden:
                # Skip channels we can't read
                continue
            except Exception as e:
                logger.warning(f"Error searching channel {channel.id}: {e}")
                continue

            # Early exit if we have enough results
            if len(results) >= limit:
                break

        # Sort by timestamp (most recent first) and limit results
        results.sort(key=lambda x: x["timestamp"], reverse=True)
        return results[:limit]


class WebhookServer:
    """
    Simple HTTP server for receiving webhooks from theblockacademy backend.
    Runs alongside the Discord bot to handle recognition-related webhooks.
    """

    def __init__(self, bot: DiscordBot):
        self.bot = bot
        self.app = web.Application()
        self.app.router.add_post('/recognition/delete-message', self.handle_delete_message)
        self.app.router.add_post('/server/delete-message', self.handle_delete_message)  # Alias for title revoke
        self.app.router.add_post('/server/gamemode-change', self.handle_gamemode_change)
        self.app.router.add_post('/server/title-grant', self.handle_title_grant)
        self.app.router.add_get('/health', self.handle_health)
        self.runner: Optional[web.AppRunner] = None

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok"})

    async def handle_delete_message(self, request: web.Request) -> web.Response:
        """Handle request to delete a Discord message."""
        # Verify API key
        auth_header = request.headers.get('Authorization', '')
        expected_key = os.getenv('SLASHAI_API_KEY')
        if expected_key and auth_header != f'Bearer {expected_key}':
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            # Accept both 'discord_message_id' and 'message_id' for flexibility
            message_id = data.get('discord_message_id') or data.get('message_id')
            channel_id = data.get('channel_id')

            if not message_id:
                return web.json_response({"error": "Missing message_id or discord_message_id"}, status=400)

            # Use the announcements channel if not specified
            if not channel_id:
                channel_id = os.getenv('RECOGNITION_ANNOUNCEMENTS_CHANNEL')

            if not channel_id:
                logger.warning("No channel ID provided and RECOGNITION_ANNOUNCEMENTS_CHANNEL not set")
                return web.json_response({"error": "No channel ID available"}, status=400)

            # Delete the message
            try:
                channel = self.bot.get_channel(int(channel_id))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(channel_id))

                if channel:
                    message = await channel.fetch_message(int(message_id))
                    await message.delete()
                    logger.info(f"Deleted Discord message {message_id} from channel {channel_id}")
                    return web.json_response({"success": True})
                else:
                    logger.warning(f"Channel {channel_id} not found")
                    return web.json_response({"error": "Channel not found"}, status=404)

            except discord.NotFound:
                logger.warning(f"Message {message_id} not found (may already be deleted)")
                return web.json_response({"success": True, "note": "Message already deleted"})
            except discord.Forbidden:
                logger.error(f"No permission to delete message {message_id}")
                return web.json_response({"error": "No permission to delete"}, status=403)

        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"Error handling delete-message webhook: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_gamemode_change(self, request: web.Request) -> web.Response:
        """Handle gamemode change announcement webhook from theblockacademy backend."""
        logger.info(f"Received gamemode-change webhook request from {request.remote}")

        # Verify API key
        auth_header = request.headers.get('Authorization', '')
        expected_key = os.getenv('SLASHAI_API_KEY')
        if expected_key and auth_header != f'Bearer {expected_key}':
            logger.warning(f"Gamemode webhook unauthorized - got '{auth_header[:20]}...' expected 'Bearer {expected_key[:10]}...'")
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            player_name = data.get('player_name')
            player_uuid = data.get('player_uuid')
            from_gamemode = data.get('from_gamemode')
            to_gamemode = data.get('to_gamemode')
            time_in_previous = data.get('time_in_previous')

            if not player_name or not to_gamemode:
                return web.json_response({"error": "Missing required fields"}, status=400)

            # Build the embed - blue color for gamemode changes
            # Color: 0x5865F2 (Discord blurple)
            embed = discord.Embed(color=0x5865F2)

            # Format: slashdaemon switched to Creative after 10m in Survival 🎮
            if time_in_previous and from_gamemode:
                description = f"{player_name} switched to {to_gamemode} after {time_in_previous} in {from_gamemode} 🎮"
            else:
                description = f"{player_name} switched to {to_gamemode} 🎮"

            # Add player avatar if UUID provided (using MC-Heads API)
            if player_uuid:
                clean_uuid = player_uuid.replace('-', '')
                avatar_url = f"https://mc-heads.net/avatar/{clean_uuid}/64"
                embed.set_author(name=description, icon_url=avatar_url)
            else:
                embed.description = description

            # Get the server-chat channel ID (default to #server-chat)
            channel_id = os.getenv('SERVER_CHAT_CHANNEL', '1452391354213859480')

            try:
                channel = self.bot.get_channel(int(channel_id))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(channel_id))

                if channel:
                    await channel.send(embed=embed)
                    logger.info(f"Announced gamemode change: {player_name} -> {to_gamemode}")
                    return web.json_response({"success": True})
                else:
                    logger.warning(f"Channel {channel_id} not found for gamemode announcement")
                    return web.json_response({"error": "Channel not found"}, status=404)

            except discord.Forbidden:
                logger.error(f"No permission to send message in channel {channel_id}")
                return web.json_response({"error": "No permission to send"}, status=403)

        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"Error handling gamemode-change webhook: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_title_grant(self, request: web.Request) -> web.Response:
        """Handle title grant announcement webhook from theblockacademy backend."""
        logger.info(f"Received title-grant webhook request from {request.remote}")

        # Verify API key
        auth_header = request.headers.get('Authorization', '')
        expected_key = os.getenv('SLASHAI_API_KEY')
        if expected_key and auth_header != f'Bearer {expected_key}':
            logger.warning(f"Title grant webhook unauthorized")
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            logger.info(f"Title grant webhook data: {data}")  # Debug: log full payload
            player_name = data.get('player_name')
            player_uuid = data.get('player_uuid')  # For avatar
            player_discord_id = data.get('player_discord_id')  # For @mention
            granted_by_discord_id = data.get('granted_by_discord_id')  # For granter @mention
            title_name = data.get('title_name')
            title_tier = data.get('title_tier', 'entry')
            title_category = data.get('title_category', 'craft')
            reason = data.get('reason')

            logger.info(f"Title grant: player_uuid={player_uuid}, player_discord_id={player_discord_id}")

            if not player_name or not title_name:
                return web.json_response({"error": "Missing required fields"}, status=400)

            # Choose emoji and color based on tier
            tier_config = {
                'entry': {'emoji': '🌱', 'color': 0x77B255},      # Green
                'bronze': {'emoji': '🥉', 'color': 0xCD7F32},     # Bronze
                'silver': {'emoji': '🥈', 'color': 0xC0C0C0},     # Silver
                'gold': {'emoji': '🥇', 'color': 0xFFD700},       # Gold
                'legendary': {'emoji': '✨', 'color': 0x9B59B6},  # Purple
            }
            config = tier_config.get(title_tier, {'emoji': '🏆', 'color': 0xFFD700})

            # Build the embed with player avatar
            embed = discord.Embed(color=config['color'])

            # Build description with optional granter mention
            description_parts = []
            if reason:
                # If reason mentions "Granted by", add granter @mention if available
                if granted_by_discord_id and "Granted by" in reason:
                    description_parts.append(f"> Granted by <@{granted_by_discord_id}>")
                else:
                    description_parts.append(f"> {reason}")

            # Set author with player's Minecraft avatar (like DeanBot)
            if player_uuid:
                # Normalize UUID (remove hyphens)
                clean_uuid = player_uuid.replace('-', '')
                # Use MC-Heads (Crafatar is unreliable)
                avatar_url = f"https://mc-heads.net/avatar/{clean_uuid}/64"
                logger.info(f"Title grant avatar URL: {avatar_url}")
                embed.set_author(
                    name=f"{player_name} earned the {title_name} title! {config['emoji']}",
                    icon_url=avatar_url
                )
                if description_parts:
                    embed.description = "\n".join(description_parts)
            else:
                logger.warning(f"Title grant: No player_uuid provided, using fallback without avatar")
                # Fallback without avatar
                embed.description = f"**{player_name}** earned the **{title_name}** title! {config['emoji']}"
                if description_parts:
                    embed.description += "\n" + "\n".join(description_parts)

            # Build mention content for notification (mentions in embeds don't notify)
            mention_content = None
            if player_discord_id:
                mention_content = f"<@{player_discord_id}>"

            # Get the server-chat channel ID (default to #server-chat)
            channel_id = os.getenv('SERVER_CHAT_CHANNEL', '1452391354213859480')

            try:
                channel = self.bot.get_channel(int(channel_id))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(channel_id))

                if channel:
                    message = await channel.send(content=mention_content, embed=embed)
                    logger.info(f"Announced title grant: {player_name} earned {title_name} (msg_id={message.id})")
                    return web.json_response({
                        "success": True,
                        "message_id": str(message.id),
                        "channel_id": str(channel.id)
                    })
                else:
                    logger.warning(f"Channel {channel_id} not found for title announcement")
                    return web.json_response({"error": "Channel not found"}, status=404)

            except discord.Forbidden:
                logger.error(f"No permission to send message in channel {channel_id}")
                return web.json_response({"error": "No permission to send"}, status=403)

        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"Error handling title-grant webhook: {e}", exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def start(self, port: int = 8000):
        """Start the webhook server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Webhook server started on port {port}")

    async def stop(self):
        """Stop the webhook server."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("Webhook server stopped")


async def main():
    """Run the bot standalone (chatbot mode) with webhook server."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("Error: DISCORD_BOT_TOKEN environment variable not set")
        print("Please set it in your .env file")
        return

    bot = DiscordBot()

    # Start webhook server if port is configured
    webhook_port = int(os.getenv("WEBHOOK_SERVER_PORT", "8000"))
    webhook_server = WebhookServer(bot)

    try:
        # Start webhook server first
        await webhook_server.start(webhook_port)

        # Then start the Discord bot (this blocks until bot disconnects)
        await bot.start(token)
    finally:
        await webhook_server.stop()


if __name__ == "__main__":
    asyncio.run(main())
