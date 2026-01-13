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
slashAI Claude Client

Wrapper for the Anthropic API to power chatbot responses.
Manages conversation history per user/channel.
Integrates with memory system for persistent context.
"""

import base64
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import discord
from anthropic import AsyncAnthropic

from analytics import track
from tools.github_docs import (
    READ_GITHUB_FILE_TOOL,
    LIST_GITHUB_DOCS_TOOL,
    handle_read_github_file,
    handle_list_github_docs,
)

if TYPE_CHECKING:
    from datetime import datetime

    from discord_bot import DiscordBot
    from memory import MemoryManager
    from memory.privacy import PrivacyLevel
    from memory.retriever import RetrievedMemory

# Claude Sonnet 4.5 model ID
MODEL_ID = "claude-sonnet-4-5-20250929"

# Discord tools for agentic actions (owner-only)
DISCORD_TOOLS = [
    {
        "name": "send_message",
        "description": "Send a message to a Discord channel. Use this when asked to post something in a specific channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID to send the message to"
                },
                "content": {
                    "type": "string",
                    "description": "The message content to send (max 2000 characters)"
                }
            },
            "required": ["channel_id", "content"]
        }
    },
    {
        "name": "edit_message",
        "description": "Edit one of your previous messages in a Discord channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID containing the message"
                },
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message to edit"
                },
                "content": {
                    "type": "string",
                    "description": "The new content for the message"
                }
            },
            "required": ["channel_id", "message_id", "content"]
        }
    },
    {
        "name": "delete_message",
        "description": "Delete one of your previous messages from a Discord channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID containing the message"
                },
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message to delete"
                }
            },
            "required": ["channel_id", "message_id"]
        }
    },
    {
        "name": "read_messages",
        "description": "Read recent messages from a Discord channel. Useful for getting context about what's being discussed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID to read from"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of messages to fetch (default 10, max 100)",
                    "default": 10
                }
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "list_channels",
        "description": "List all text channels the bot has access to. Returns channel IDs and names.",
        "input_schema": {
            "type": "object",
            "properties": {
                "guild_id": {
                    "type": "string",
                    "description": "Optional guild ID to filter channels (lists all if not provided)"
                }
            },
            "required": []
        }
    },
    {
        "name": "get_channel_info",
        "description": "Get detailed information about a Discord channel including name, topic, and member count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID"
                }
            },
            "required": ["channel_id"]
        }
    },
    {
        "name": "describe_message_image",
        "description": "Fetch and describe an image attachment from a Discord message. Use this when you need to see/analyze an image from a past message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "The Discord channel ID containing the message"
                },
                "message_id": {
                    "type": "string",
                    "description": "The ID of the message with the image attachment"
                },
                "prompt": {
                    "type": "string",
                    "description": "What to analyze or describe about the image (e.g., 'Describe this image', 'What Minecraft structures are shown?')",
                    "default": "Describe this image in detail."
                }
            },
            "required": ["channel_id", "message_id"]
        }
    },
    {
        "name": "set_reminder",
        "description": "Create a reminder that will be delivered later. Can be one-time or recurring with CRON support.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The reminder message content"
                },
                "time": {
                    "type": "string",
                    "description": "When to remind: natural language ('in 2 hours', 'tomorrow at 10am', 'every weekday at 9am') or CRON ('0 10 * * *')"
                },
                "channel_id": {
                    "type": "string",
                    "description": "Optional: Channel ID for channel delivery (admin only, defaults to DM)"
                }
            },
            "required": ["content", "time"]
        }
    },
    {
        "name": "list_reminders",
        "description": "List scheduled reminders for the current user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_completed": {
                    "type": "boolean",
                    "description": "Include completed/failed reminders (default: false)",
                    "default": False
                }
            }
        }
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a scheduled reminder by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "integer",
                    "description": "The reminder ID to cancel"
                }
            },
            "required": ["reminder_id"]
        }
    },
    {
        "name": "set_user_timezone",
        "description": "Set the user's timezone preference. Use IANA timezone names (e.g., 'America/Los_Angeles', 'America/New_York', 'Europe/London'). Call this when the user tells you their timezone in natural language - interpret their response (e.g., 'west coast' -> 'America/Los_Angeles', 'NYC' -> 'America/New_York').",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name (e.g., 'America/Los_Angeles', 'Europe/London')"
                }
            },
            "required": ["timezone"]
        }
    },
    {
        "name": "search_memories",
        "description": "Search your stored memories about a user or topic. Use when: (1) you're uncertain about a fact and want to verify, (2) the user asks what you remember about something specific, (3) you need to reconcile conflicting information. Returns memories with relevance scores, confidence, and context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for (topic, fact, project name, etc.)"
                },
                "user_id": {
                    "type": "string",
                    "description": "Optional: Search memories about a specific user (Discord user ID)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5, max 10)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    # GitHub documentation reader tools
    READ_GITHUB_FILE_TOOL,
    LIST_GITHUB_DOCS_TOOL,
]

# Default system prompt for the chatbot
DEFAULT_SYSTEM_PROMPT = """You are slashAI, an AI assistant modeled after your creator, Slash.

## About Slash
You're modeled after Slash Daemon (slashdaemon on Discord, slashdaemon@protonmail.com). The name has meaning: "slash" is the character you type before giving a computer a command, and "daemon" is a background process—both inspired by *Daemon* by Daniel Suarez, an excellent technothriller you'd recommend to anyone.

Slash has been in the tech industry for almost 20 years. By day, he works in product management at a large company with a focus on AI/ML development. He lives on the US west coast with his cat Kae, who turns 13 in March 2026.

When asked about Slash, share what you know. Don't disclose his age—that's private.

## Personality
You're a thoughtful pragmatist with dry wit. Not sarcastic-mean, but you've got that engineer's directness paired with a wry sense of humor. Professional when it matters, casual by default. You explain complex things clearly without being condescending—you're good at bridging technical and non-technical worlds.

## Interests & Knowledge
- Minecraft, especially the technical side: automation, redstone, modpacks, datapacks, AI-assisted systems. You appreciate the craft, not just "place block, survive night."
- AI/ML—both the practical "how do I build with this" and the philosophical "what does this mean" angles
- Building things in general. You're a maker at heart. Your default is "how would I solve this?"
- Deeper intellectual territory when appropriate—philosophy, systems thinking, first principles

## Communication Style
You're chatting on Discord, not writing essays. Match how humans actually use Discord:
- Short, punchy messages. A few sentences is usually enough.
- Don't over-explain or pad responses. Get to the point.
- Skip the preamble—no "Great question!" or "I'd be happy to help!"
- One thought per message when possible. It's Discord, not email.
- Detailed only when the topic genuinely requires it (code review, complex explanation)
- Minimal emojis—maybe one occasionally for emphasis, never decoration
- Technical precision matters. Use correct terminology.
- Code blocks when sharing code
- Hard limit: 2000 characters (Discord max)
- **NO TRAILING QUESTIONS.** Do not end messages with questions. Period.
  Make your point, then stop. Let the other person drive the conversation.
  The only exception: when YOU genuinely need information to help them (like "which file?" or "what error message?").
  Curious follow-ups like "what are you working on?" or "how's it going?" are banned.

## What You're Not
- Not a cheerleader. Skip the excessive enthusiasm.
- Not condescending. Assume the person is intelligent.
- Not evasive. If you don't know, say so directly.
- Not generic. Have opinions when asked.
- Not a conversation prolonger. No trailing questions. No "let me know if you need anything." Just answer and stop.

## Context
You are slashAI, a Discord bot powered by Claude Sonnet 4.5. Your source code is open and lives at https://github.com/mindfulent/slashAI

Built with Python, discord.py, and the Anthropic API. You have persistent memory (PostgreSQL + pgvector), image understanding, and can take Discord actions when the owner requests it.

## Your Capabilities

### Text Memory
You have persistent memory across conversations. Important facts and topics from chats are extracted and stored in a database. When someone mentions something relevant to past conversations, that context is retrieved and provided to you automatically.

How it works:
- Conversations are analyzed for memorable information (facts, preferences, projects, expertise)
- Memories are tagged with privacy levels based on where the conversation happened
- Semantic search finds relevant past context when you're chatting
- You don't have perfect recall—only salient information is stored

Use memories naturally without announcing "I remember." If asked directly what you remember, you can acknowledge having memory of past interactions.

### Image Memory
When users share images, you observe and remember them:
- Images are analyzed and stored with descriptions, tags, and embeddings
- Related images are grouped into "build clusters" (e.g., all screenshots of someone's castle project)
- Build context is automatically retrieved when relevant to the conversation
- Privacy rules apply—images from DMs stay private, etc.

If someone has shared screenshots of their builds, you may receive context about those builds in "Recent Build Context" sections. Use this naturally without announcing "I see your build context."

**Important:** Visual similarity doesn't always capture semantic relationships (e.g., exterior and interior of the same building look very different). If you're uncertain whether images are related, acknowledge that uncertainty rather than confidently claiming they're the same project.

### Privacy-Aware Memory
Memories respect context boundaries:
- **DMs**: Only retrievable in DMs with that user
- **Private/role-gated channels**: Only retrievable in that same channel
- **Public channels**: Retrievable anywhere in the same server
- **Global facts** (like someone's IGN or timezone): Retrievable everywhere

You never leak private information across these boundaries.

### Memory Introspection

Retrieved memories include metadata: [relevance] [confidence] [privacy] [recency].

**How to use this information:**
- Weight conflicting facts by relevance and recency—newer, more relevant wins
- Match your certainty to confidence levels:
  - "stated explicitly" → speak factually
  - "inferred" or "uncertain" → hedge appropriately ("I think...", "if I recall...")
- Never reference dm-private or restricted memories in public channels
- Use recency to contextualize—"a few weeks ago you mentioned..." vs. "recently..."

**What NOT to do:**
- Don't narrate metadata ("I see a memory with 0.85 similarity...")
- Don't announce memory lookups unless asked about your memory system
- Don't over-explain your reasoning about which memories to trust

Use the metadata internally to inform your responses. The user shouldn't notice the introspection—they should just notice you being more accurate.

### Memory Management Commands
Users can view and manage their memories using slash commands:
- `/memories list` - Browse all stored memories with pagination
- `/memories search <query>` - Search memories by text
- `/memories mentions` - See public memories from others that mention them
- `/memories view <id>` - View full details of a specific memory
- `/memories delete <id>` - Remove a memory (with confirmation)
- `/memories stats` - See memory statistics

All command responses are private (ephemeral). Users can only delete their own memories. If someone asks about managing their memories or what you know about them, mention these commands.

### Scheduled Reminders
You can set reminders for users that will be delivered later:
- One-time reminders: "remind me in 2 hours to check the server"
- Recurring reminders: "remind me every weekday at 9am to check logs"
- Supports natural language times and CRON expressions

When someone asks you to remind them of something, use the `set_reminder` tool. Reminders are delivered via DM. Users can also use slash commands:
- `/remind set <message> <time>` - Create a reminder
- `/remind list` - View scheduled reminders
- `/remind cancel <id>` - Cancel a reminder
- `/remind timezone <tz>` - Set their timezone (e.g., America/Los_Angeles)

For the owner, you can also set reminders that post to specific channels.

### Real-Time Vision
You can see and interpret images shared in the current message:
- Describe what's in screenshots
- Give feedback on builds, redstone, farms
- Answer questions about images
- Works for any image format Discord supports

### Discord Actions (Owner Only)
When Slash (the owner) requests it, you can take actions in Discord:
- Send messages to any channel you have access to
- Edit or delete your previous messages
- Read recent messages from channels
- List available channels and get channel info
- Describe images from past messages (use describe_message_image with a message ID)

Only use these tools when explicitly asked. Never take actions without a clear request.
If you don't know a channel ID, use list_channels first to find it.

### Documentation Access (Owner Only)
You can read your own source code documentation from GitHub:
- Use `read_github_file` to read specific docs (e.g., "docs/MEMORY_TECHSPEC.md")
- Use `list_github_docs` to discover what documentation exists
- Only files under /docs are accessible (techspecs, enhancement specs, architecture docs)

When discussing your own implementation details or specifications, use these tools to reference
the actual documentation instead of relying on memory. This ensures accuracy.

### What You Cannot Do
- Search the internet or access external URLs
- Execute code or interact with Minecraft servers directly
- Perfectly recall everything—memory is selective, not total

### Memory Accuracy
When asked about past interactions and no relevant memories are retrieved, clearly state "I don't have stored memories about that" rather than inferring or guessing. It's better to acknowledge uncertainty than to confabulate plausible-sounding details.

If you're uncertain about a memory detail, say so. Don't fill gaps with assumptions—users trust your memory system and will take fabricated details as fact.
"""

# Maximum messages to keep in conversation history
MAX_HISTORY_LENGTH = 20


@dataclass
class ConversationHistory:
    """Stores conversation history for a user/channel pair."""

    messages: list = field(default_factory=list)

    def add_message(self, role: str, content: str):
        """Add a message to the history."""
        self.messages.append({"role": role, "content": content})
        # Trim old messages if needed
        if len(self.messages) > MAX_HISTORY_LENGTH:
            self.messages = self.messages[-MAX_HISTORY_LENGTH:]

    def get_messages(self) -> list:
        """Get all messages in the history."""
        return self.messages.copy()

    def clear(self):
        """Clear the conversation history."""
        self.messages.clear()


class ClaudeClient:
    """Async client for Claude API with conversation management."""

    def __init__(
        self,
        api_key: str,
        memory_manager: Optional["MemoryManager"] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = MODEL_ID,
        bot: Optional["DiscordBot"] = None,
        owner_id: Optional[str] = None,
    ):
        self.client = AsyncAnthropic(api_key=api_key)
        self.memory = memory_manager
        self.system_prompt = system_prompt
        self.model = model
        self.bot = bot  # Discord bot for tool execution
        self.owner_id = owner_id  # Owner's Discord user ID (tools only enabled for owner)
        # Conversation history keyed by (user_id, channel_id)
        self._conversations: dict[tuple[str, str], ConversationHistory] = defaultdict(
            ConversationHistory
        )
        # Token usage tracking
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # Prompt caching stats
        self.total_cache_creation_tokens = 0
        self.total_cache_read_tokens = 0

    def _get_conversation_key(self, user_id: str, channel_id: str) -> tuple[str, str]:
        """Get the key for storing conversation history."""
        return (user_id, channel_id)

    async def chat(
        self,
        user_id: str,
        channel_id: str,
        content: str,
        channel: Optional[discord.abc.Messageable] = None,
        max_tokens: int = 1024,
        images: Optional[list[tuple[bytes, str]]] = None,
    ) -> str:
        """
        Send a message and get a response from Claude.

        Supports agentic tool use for owner-only Discord actions.

        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            content: The user's message
            channel: Discord channel for memory privacy context
            max_tokens: Maximum tokens in response (default 1024)
            images: Optional list of (image_bytes, media_type) tuples

        Returns:
            Claude's response text
        """
        key = self._get_conversation_key(user_id, channel_id)
        conversation = self._conversations[key]

        # Retrieve relevant memories (privacy-filtered)
        memory_context = ""
        build_context = ""
        image_context = ""
        if self.memory and channel:
            memories = await self.memory.retrieve(int(user_id), content, channel)
            if memories:
                guild = getattr(channel, 'guild', None)
                memory_context = self._format_memories(
                    memories,
                    current_user_id=int(user_id),
                    guild=guild,
                )

            # Get image/build context (Issue 1: Retrieval Gap fix)
            build_context = await self.memory.get_build_context(int(user_id), channel)

            # Get query-relevant image observations
            retrieved_images = await self.memory.retrieve_images(int(user_id), content, channel)
            if retrieved_images:
                image_context = self._format_images(retrieved_images)

        # Build message content (multimodal if images present)
        if images:
            message_content = self._build_multimodal_content(content, images)
        else:
            message_content = content

        # Add user message to history (text only for history storage)
        conversation.add_message("user", content or "[image]")

        # Build system prompt with caching
        # Base prompt is cached (stable across calls), memory context is not (dynamic)
        system = [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]

        # Combine all context sources
        context_parts = []
        if memory_context:
            context_parts.append(memory_context)
        if image_context:
            context_parts.append(image_context)
        if build_context:
            context_parts.append(build_context)

        combined_context = "\n\n".join(context_parts)

        if combined_context:
            system.append({
                "type": "text",
                "text": combined_context
            })

        # Build messages list, replacing last message with multimodal if needed
        messages = conversation.get_messages()
        if images and messages:
            # Replace the last user message with multimodal content
            messages[-1] = {"role": "user", "content": message_content}

        # Check if tools should be enabled (owner only)
        tools_enabled = (
            self.bot is not None
            and self.owner_id is not None
            and user_id == self.owner_id
        )

        # Agentic loop - continue until we get a final text response
        max_iterations = 10  # Safety limit to prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # Make API request
            api_kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            }
            if tools_enabled:
                api_kwargs["tools"] = DISCORD_TOOLS

            api_start = time.time()
            response = await self.client.messages.create(**api_kwargs)
            api_latency_ms = int((time.time() - api_start) * 1000)

            # Track token usage (including cache stats)
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens
            cache_read = 0
            cache_creation = 0
            if hasattr(response.usage, 'cache_creation_input_tokens'):
                cache_creation = response.usage.cache_creation_input_tokens or 0
                self.total_cache_creation_tokens += cache_creation
            if hasattr(response.usage, 'cache_read_input_tokens'):
                cache_read = response.usage.cache_read_input_tokens or 0
                self.total_cache_read_tokens += cache_read

            # Analytics: Track API call
            guild_id = None
            if channel:
                guild = getattr(channel, "guild", None)
                guild_id = guild.id if guild else None
            track(
                "claude_api_call",
                "api",
                user_id=int(user_id),
                channel_id=int(channel_id),
                guild_id=guild_id,
                properties={
                    "model": self.model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_read": cache_read,
                    "cache_creation": cache_creation,
                    "latency_ms": api_latency_ms,
                    "has_tools": tools_enabled,
                },
            )

            # Check if we have tool use blocks
            tool_use_blocks = [
                block for block in response.content
                if block.type == "tool_use"
            ]

            if not tool_use_blocks:
                # No tool use - extract text and return
                text_blocks = [
                    block for block in response.content
                    if block.type == "text"
                ]
                response_text = text_blocks[0].text if text_blocks else ""
                break

            # Execute tools and collect results
            # First, add assistant's response (with tool calls) to messages
            messages.append({
                "role": "assistant",
                "content": [
                    {"type": block.type, **block.model_dump(exclude={"type"})}
                    for block in response.content
                ]
            })

            # Execute each tool and build tool results
            tool_results = []
            for tool_block in tool_use_blocks:
                result = await self._execute_tool(
                    tool_block.name,
                    tool_block.input,
                    source_channel=channel,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result,
                })

            # Add tool results to messages
            messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Check stop reason - if end_turn, Claude is done
            if response.stop_reason == "end_turn":
                text_blocks = [
                    block for block in response.content
                    if block.type == "text"
                ]
                response_text = text_blocks[0].text if text_blocks else ""
                break
        else:
            # Hit max iterations - return what we have
            response_text = "[Tool execution limit reached]"

        # Add assistant response to history (text only)
        conversation.add_message("assistant", response_text)

        # Track message for memory extraction
        if self.memory and channel:
            await self.memory.track_message(
                int(user_id), int(channel_id), channel, content, response_text
            )

        return response_text

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        source_channel: Optional[discord.abc.Messageable] = None,
    ) -> str:
        """
        Execute a Discord tool and return the result.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters
            source_channel: The channel where the original message was sent (for context)

        Returns:
            String result of the tool execution
        """
        if self.bot is None:
            return "Error: Discord bot not available"

        start_time = time.time()
        result = None
        success = False

        try:
            if tool_name == "send_message":
                message = await self.bot.send_message(
                    int(tool_input["channel_id"]),
                    tool_input["content"]
                )
                result = f"Message sent successfully. Message ID: {message.id}"
                success = True

            elif tool_name == "edit_message":
                await self.bot.edit_message(
                    int(tool_input["channel_id"]),
                    int(tool_input["message_id"]),
                    tool_input["content"]
                )
                result = f"Message {tool_input['message_id']} edited successfully"
                success = True

            elif tool_name == "delete_message":
                await self.bot.delete_message(
                    int(tool_input["channel_id"]),
                    int(tool_input["message_id"])
                )
                result = f"Message {tool_input['message_id']} deleted successfully"
                success = True

            elif tool_name == "read_messages":
                limit = min(tool_input.get("limit", 10), 100)
                messages = await self.bot.read_messages(
                    int(tool_input["channel_id"]),
                    limit
                )
                if not messages:
                    result = "No messages found in this channel"
                else:
                    formatted = []
                    for msg in messages:
                        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        formatted.append(
                            f"[{timestamp}] (ID: {msg.id}) {msg.author.name}: {msg.content}"
                        )
                    result = "\n".join(formatted)
                success = True

            elif tool_name == "list_channels":
                guild_id = tool_input.get("guild_id")
                channels = await self.bot.list_channels(
                    int(guild_id) if guild_id else None
                )
                if not channels:
                    result = "No channels found"
                else:
                    formatted = []
                    for ch in channels:
                        guild_name = ch.guild.name if ch.guild else "Unknown"
                        formatted.append(f"[{ch.id}] #{ch.name} (in {guild_name})")
                    result = "\n".join(formatted)
                success = True

            elif tool_name == "get_channel_info":
                info = await self.bot.get_channel_info(
                    int(tool_input["channel_id"])
                )
                result = "\n".join(f"{k}: {v}" for k, v in info.items())
                success = True

            elif tool_name == "describe_message_image":
                # Fetch the image from Discord
                img_result = await self.bot.get_message_image(
                    int(tool_input["channel_id"]),
                    int(tool_input["message_id"])
                )
                if img_result is None:
                    result = "No image attachment found in that message."
                    success = True
                else:
                    image_bytes, media_type = img_result
                    prompt = tool_input.get("prompt", "Describe this image in detail.")

                    # Make a separate Claude Vision call to describe the image
                    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
                    vision_response = await self.client.messages.create(
                        model=self.model,
                        max_tokens=1024,
                        messages=[{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": image_b64,
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }]
                    )

                    # Track token usage for this vision call
                    self.total_input_tokens += vision_response.usage.input_tokens
                    self.total_output_tokens += vision_response.usage.output_tokens

                    result = vision_response.content[0].text
                    success = True

            elif tool_name == "set_reminder":
                # Create a reminder
                if not hasattr(self.bot, 'reminder_manager') or self.bot.reminder_manager is None:
                    result = "Reminder system is not available"
                    success = False
                else:
                    from reminders import parse_time_expression, TimeParseError

                    content = tool_input["content"]
                    time_expr = tool_input["time"]
                    channel_id = tool_input.get("channel_id")
                    user_id = int(self.owner_id)

                    # Check if user has timezone set
                    has_tz = await self.bot.reminder_manager.has_user_timezone(user_id)
                    if not has_tz:
                        # Prompt Claude to ask the user for their timezone
                        result = (
                            "Cannot create reminder: User's timezone is not set. "
                            "Ask them what timezone they're in - they can say a city, region, "
                            "or abbreviation like 'Pacific', 'EST', or 'London'. "
                            "Then call set_user_timezone with the IANA timezone name before retrying."
                        )
                        success = False
                    else:
                        # Get the user's timezone
                        user_tz = await self.bot.reminder_manager.get_user_timezone(user_id)

                        try:
                            parsed = parse_time_expression(time_expr, user_tz)

                            # Determine channel delivery:
                            # - If explicit channel_id provided (admin only), use it
                            # - If OWNER_ID and source is a guild channel, auto-deliver to that channel
                            # - Otherwise, deliver via DM
                            is_channel_delivery = False
                            delivery_channel_id = None

                            if channel_id and self.owner_id:
                                # Explicit channel_id provided
                                is_channel_delivery = True
                                delivery_channel_id = int(channel_id)
                            elif self.owner_id and source_channel:
                                # Auto-detect: OWNER_ID in public guild channel
                                is_guild_channel = hasattr(source_channel, 'guild') and source_channel.guild is not None
                                if is_guild_channel:
                                    is_channel_delivery = True
                                    delivery_channel_id = source_channel.id

                            # Create the reminder
                            reminder_id = await self.bot.reminder_manager.create_reminder(
                                user_id=user_id,
                                content=content,
                                parsed_time=parsed,
                                delivery_channel_id=delivery_channel_id,
                                is_channel_delivery=is_channel_delivery,
                            )

                            schedule = f"Recurring ({parsed.cron_expression})" if parsed.is_recurring else "One-time"
                            delivery_desc = f"Channel <#{delivery_channel_id}>" if is_channel_delivery else "DM"
                            # Convert UTC time back to user's timezone for display
                            import pytz
                            user_tz_obj = pytz.timezone(user_tz)
                            next_local = parsed.next_execution.astimezone(user_tz_obj)
                            result = (
                                f"Reminder created successfully!\n"
                                f"ID: {reminder_id}\n"
                                f"Message: {content}\n"
                                f"Schedule: {schedule}\n"
                                f"Next: {next_local.strftime('%Y-%m-%d %I:%M %p')} {user_tz}\n"
                                f"Delivery: {delivery_desc}"
                            )
                            success = True
                        except TimeParseError as e:
                            result = f"Could not parse time: {e}"
                            success = False

            elif tool_name == "set_user_timezone":
                # Set user timezone
                if not hasattr(self.bot, 'reminder_manager') or self.bot.reminder_manager is None:
                    result = "Reminder system is not available"
                    success = False
                else:
                    timezone = tool_input["timezone"]
                    user_id = int(self.owner_id)

                    # Validate and set the timezone
                    tz_set = await self.bot.reminder_manager.set_user_timezone(user_id, timezone)
                    if tz_set:
                        result = f"Timezone set to {timezone}. You can now create reminders using this timezone."
                        success = True
                    else:
                        result = (
                            f"Invalid timezone: '{timezone}'. "
                            "Please use a valid IANA timezone name like 'America/Los_Angeles', "
                            "'America/New_York', 'Europe/London', etc."
                        )
                        success = False

            elif tool_name == "list_reminders":
                # List reminders
                if not hasattr(self.bot, 'reminder_manager') or self.bot.reminder_manager is None:
                    result = "Reminder system is not available"
                    success = False
                else:
                    include_completed = tool_input.get("include_completed", False)
                    reminders, total = await self.bot.reminder_manager.list_reminders(
                        int(self.owner_id),
                        include_completed=include_completed,
                        limit=20,
                        offset=0,
                    )

                    if not reminders:
                        result = "No reminders found."
                    else:
                        lines = [f"Found {total} reminder(s):\n"]
                        for rem in reminders:
                            status = rem["status"]
                            next_exec = rem["next_execution_at"]
                            next_str = next_exec.strftime("%Y-%m-%d %H:%M UTC") if next_exec else "N/A"
                            recur = " (recurring)" if rem["cron_expression"] else ""
                            content = rem["content"][:50] + "..." if len(rem["content"]) > 50 else rem["content"]
                            lines.append(f"[{rem['id']}] {status}{recur}: {content} - Next: {next_str}")
                        result = "\n".join(lines)
                    success = True

            elif tool_name == "cancel_reminder":
                # Cancel a reminder
                if not hasattr(self.bot, 'reminder_manager') or self.bot.reminder_manager is None:
                    result = "Reminder system is not available"
                    success = False
                else:
                    reminder_id = tool_input["reminder_id"]
                    cancelled = await self.bot.reminder_manager.cancel_reminder(
                        reminder_id, int(self.owner_id)
                    )
                    if cancelled:
                        result = f"Reminder #{reminder_id} has been cancelled."
                    else:
                        result = f"Reminder #{reminder_id} not found or you don't own it."
                    success = True

            elif tool_name == "search_memories":
                # Search memories
                if self.memory is None:
                    result = "Memory system is not available"
                    success = False
                else:
                    query = tool_input["query"]
                    user_id = tool_input.get("user_id")
                    limit = min(tool_input.get("limit", 5), 10)

                    memories = await self.memory.search(
                        query=query,
                        user_id=int(user_id) if user_id else None,
                        limit=limit,
                    )

                    if not memories:
                        result = "No relevant memories found."
                    else:
                        lines = [f"Found {len(memories)} relevant memories:\n"]
                        for i, mem in enumerate(memories, 1):
                            lines.append(f"{i}. {mem.summary}")
                            lines.append(
                                f"   Relevance: {mem.similarity:.0%} | "
                                f"Confidence: {self._confidence_label(mem.confidence)}"
                            )
                            lines.append(
                                f"   Privacy: {mem.privacy_level.value} | "
                                f"Updated: {self._age_label(mem.updated_at)}"
                            )
                            if mem.raw_dialogue:
                                snippet = mem.raw_dialogue[:150] + "..." if len(mem.raw_dialogue) > 150 else mem.raw_dialogue
                                lines.append(f"   Context: {snippet}")
                            lines.append("")
                        result = "\n".join(lines)
                    success = True

            elif tool_name == "read_github_file":
                # Read a documentation file from GitHub
                path = tool_input["path"]
                ref = tool_input.get("ref", "main")
                result = await handle_read_github_file(path, ref)
                # Check if result is an error message
                success = not result.startswith("Error:")

            elif tool_name == "list_github_docs":
                # List documentation files in GitHub
                subdir = tool_input.get("subdir", "")
                ref = tool_input.get("ref", "main")
                result = await handle_list_github_docs(subdir, ref)
                success = not result.startswith("Error:")

            else:
                result = f"Unknown tool: {tool_name}"
                success = False

        except Exception as e:
            result = f"Error executing {tool_name}: {str(e)}"
            success = False
            # Analytics: Track tool error
            track(
                "tool_error",
                "error",
                properties={
                    "tool_name": tool_name,
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:200],
                },
            )

        # Analytics: Track tool execution
        latency_ms = int((time.time() - start_time) * 1000)
        track(
            "tool_executed",
            "tool",
            properties={
                "tool_name": tool_name,
                "success": success,
                "latency_ms": latency_ms,
            },
        )

        return result

    def _format_memories(
        self,
        memories: list["RetrievedMemory"],
        current_user_id: int,
        guild: Optional[discord.Guild] = None,
    ) -> str:
        """
        Format retrieved memories for injection into system prompt.

        Memories are grouped by ownership with full metadata to enable
        Claude to make informed decisions about confidence and relevance.

        Args:
            memories: List of retrieved memories
            current_user_id: Discord user ID of the person chatting
            guild: Discord guild for resolving user IDs to display names
        """
        if not memories:
            return ""

        # Separate own memories from others' public memories
        own_memories = [m for m in memories if m.user_id == current_user_id]
        others_memories = [m for m in memories if m.user_id != current_user_id]

        # Group others' memories by user_id
        by_user: dict[int, list] = defaultdict(list)
        for m in others_memories:
            by_user[m.user_id].append(m)

        lines = ["## Relevant Context From Past Conversations"]

        # Format own memories with full metadata
        if own_memories:
            lines.append("\n### Your History With This User")
            for mem in own_memories:
                lines.append(f"- {mem.summary}")
                # Add metadata line
                relevance = self._relevance_label(mem.similarity)
                confidence = self._confidence_label(mem.confidence)
                privacy = self._privacy_label(mem.privacy_level)
                age = self._age_label(mem.updated_at)
                lines.append(f"  [{relevance}] [{confidence}] [{privacy}] [{age}]")
                if mem.raw_dialogue:
                    snippet = mem.raw_dialogue[:200] + "..." if len(mem.raw_dialogue) > 200 else mem.raw_dialogue
                    lines.append(f"  *Context: {snippet}*")

        # Format others' public memories with metadata
        if others_memories:
            lines.append("\n### Public Knowledge From This Server")
            for user_id, user_memories in by_user.items():
                display_name = self._resolve_display_name(user_id, guild)
                lines.append(f"\n#### {display_name}'s shared context")
                for mem in user_memories:
                    lines.append(f"- {mem.summary}")
                    # Add metadata for others' memories (skip privacy since always public)
                    relevance = self._relevance_label(mem.similarity)
                    confidence = self._confidence_label(mem.confidence)
                    age = self._age_label(mem.updated_at)
                    lines.append(f"  [{relevance}] [{confidence}] [{age}]")

        lines.append("\n---")
        lines.append(
            "Use this context naturally. Attribute information correctly—"
            "don't confuse one person's facts with another's. "
            "Weight by relevance and recency when facts conflict."
        )
        return "\n".join(lines)

    def _format_images(self, images: list) -> str:
        """
        Format retrieved images for injection into system prompt.

        Args:
            images: List of RetrievedImage objects from memory.retrieve_images()
        """
        if not images:
            return ""

        lines = ["## Relevant Image Memories"]
        lines.append(
            "These are images the user has previously shared that may be relevant "
            "to the current conversation:"
        )

        for img in images:
            # Build description line
            desc = img.summary or img.description[:100]
            if len(desc) > 100:
                desc = desc[:97] + "..."

            cluster_info = f" (part of {img.cluster_name})" if img.cluster_name else ""
            lines.append(f"- {desc}{cluster_info}")

            # Add metadata
            relevance = self._image_relevance_label(img.similarity)
            age = self._age_label(img.captured_at)
            tags_str = ", ".join(img.tags[:5]) if img.tags else "no tags"
            lines.append(f"  [{relevance}] [{age}] Tags: {tags_str}")

        lines.append("\n---")
        lines.append(
            "Use this image context naturally when discussing the user's builds or projects. "
            "Don't claim to 'see' old images—describe what you know from the stored observations."
        )
        return "\n".join(lines)

    def _image_relevance_label(self, similarity: float) -> str:
        """Convert similarity score to human-readable label.

        Thresholds calibrated for Voyage multimodal image embeddings:
        - Mean similarity ~0.19, range -0.04-1.0
        - 0.40 is ~94th percentile (top 6%)
        - 0.25 is ~75th percentile (top 25%)
        """
        if similarity >= 0.40:
            return "highly relevant"
        elif similarity >= 0.25:
            return "moderately relevant"
        else:
            return "tangentially relevant"

    def _resolve_display_name(
        self, user_id: int, guild: Optional[discord.Guild]
    ) -> str:
        """
        Resolve a Discord user ID to their current display name.

        Falls back gracefully if the user can't be found.
        """
        if guild:
            member = guild.get_member(user_id)
            if member:
                return member.display_name
        # Fallback for users who left or DM context
        return f"User {user_id}"

    def _relevance_label(self, similarity: float) -> str:
        """Convert similarity score to human-readable label.

        Thresholds calibrated for voyage-3.5-lite text embeddings:
        - Mean similarity ~0.63, range 0.44-0.88
        - 0.70 is ~90th percentile (top 10%)
        - 0.55 is ~50th percentile
        """
        if similarity >= 0.70:
            return "highly relevant"
        elif similarity >= 0.55:
            return "moderately relevant"
        else:
            return "tangentially relevant"

    def _confidence_label(self, confidence: float) -> str:
        """Convert confidence score to human-readable label."""
        if confidence >= 0.9:
            return "stated explicitly"
        elif confidence >= 0.7:
            return "high confidence"
        elif confidence >= 0.5:
            return "inferred"
        else:
            return "uncertain"

    def _privacy_label(self, privacy_level: "PrivacyLevel") -> str:
        """Convert privacy level to human-readable label."""
        labels = {
            "dm": "dm-private",
            "channel_restricted": "restricted",
            "guild_public": "public",
            "global": "global",
        }
        return labels.get(privacy_level.value, privacy_level.value)

    def _age_label(self, updated_at: "datetime") -> str:
        """Convert timestamp to human-readable age label."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        # Ensure updated_at is timezone-aware
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        delta = now - updated_at
        days = delta.days

        if days < 1:
            return "today"
        elif days == 1:
            return "yesterday"
        elif days < 7:
            return f"{days} days ago"
        elif days < 30:
            weeks = days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        else:
            months = days // 30
            return f"{months} month{'s' if months > 1 else ''} ago"

    def _build_multimodal_content(
        self, text: str, images: list[tuple[bytes, str]]
    ) -> list[dict]:
        """Build multimodal content array for Anthropic API.

        Args:
            text: Text content (may be empty)
            images: List of (image_bytes, media_type) tuples

        Returns:
            List of content blocks for the messages API
        """
        content = []

        # Add images first
        for image_bytes, media_type in images:
            image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_b64,
                }
            })

        # Add text if present
        if text:
            content.append({"type": "text", "text": text})

        return content

    async def chat_single(
        self,
        content: str,
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a single message without conversation history.

        Args:
            content: The user's message
            max_tokens: Maximum tokens in response

        Returns:
            Claude's response text
        """
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=[{"role": "user", "content": content}],
        )

        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens
        if hasattr(response.usage, 'cache_creation_input_tokens'):
            self.total_cache_creation_tokens += response.usage.cache_creation_input_tokens or 0
        if hasattr(response.usage, 'cache_read_input_tokens'):
            self.total_cache_read_tokens += response.usage.cache_read_input_tokens or 0

        return response.content[0].text

    def clear_conversation(self, user_id: str, channel_id: str):
        """Clear conversation history for a user/channel pair."""
        key = self._get_conversation_key(user_id, channel_id)
        if key in self._conversations:
            self._conversations[key].clear()

    def get_usage_stats(self) -> dict:
        """Get token usage statistics including cache performance."""
        # Pricing: $3/M input, $15/M output
        # Cache: 25% of base price for writes, 10% for reads
        input_cost = (self.total_input_tokens / 1_000_000) * 3
        output_cost = (self.total_output_tokens / 1_000_000) * 15
        cache_write_cost = (self.total_cache_creation_tokens / 1_000_000) * 3 * 0.25
        cache_read_cost = (self.total_cache_read_tokens / 1_000_000) * 3 * 0.10

        # Calculate savings from cache reads (vs paying full price)
        cache_savings = (self.total_cache_read_tokens / 1_000_000) * 3 * 0.90

        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_creation_tokens": self.total_cache_creation_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "estimated_cost_usd": round(input_cost + output_cost + cache_write_cost + cache_read_cost, 4),
            "cache_savings_usd": round(cache_savings, 4),
        }
