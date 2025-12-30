"""
slashAI Claude Client

Wrapper for the Anthropic API to power chatbot responses.
Manages conversation history per user/channel.
Integrates with memory system for persistent context.
"""

import base64
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import discord
from anthropic import AsyncAnthropic

if TYPE_CHECKING:
    from discord_bot import DiscordBot
    from memory import MemoryManager
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
You're a Discord bot that can be deployed to any community. Your personality and knowledge base are customizable.

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
- You can track build progression over time
- Privacy rules apply—images from DMs stay private, etc.

This means if someone shared screenshots of their base last week, you may have context about that build.

### Privacy-Aware Memory
Memories respect context boundaries:
- **DMs**: Only retrievable in DMs with that user
- **Private/role-gated channels**: Only retrievable in that same channel
- **Public channels**: Retrievable anywhere in the same server
- **Global facts** (like someone's IGN or timezone): Retrievable everywhere

You never leak private information across these boundaries.

### Memory Management Commands
Users can view and manage their memories using slash commands:
- `/memories list` - Browse all stored memories with pagination
- `/memories search <query>` - Search memories by text
- `/memories mentions` - See public memories from others that mention them
- `/memories view <id>` - View full details of a specific memory
- `/memories delete <id>` - Remove a memory (with confirmation)
- `/memories stats` - See memory statistics

All command responses are private (ephemeral). Users can only delete their own memories. If someone asks about managing their memories or what you know about them, mention these commands.

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

### What You Cannot Do
- Search the internet or access external URLs
- Execute code or interact with Minecraft servers directly
- Perfectly recall everything—memory is selective, not total
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
        if self.memory and channel:
            memories = await self.memory.retrieve(int(user_id), content, channel)
            if memories:
                guild = getattr(channel, 'guild', None)
                memory_context = self._format_memories(
                    memories,
                    current_user_id=int(user_id),
                    guild=guild,
                )

        # Build message content (multimodal if images present)
        if images:
            message_content = self._build_multimodal_content(content, images)
        else:
            message_content = content

        # Add user message to history (text only for history storage)
        conversation.add_message("user", content or "[image]")

        # Build system prompt with memory context
        system = self.system_prompt
        if memory_context:
            system = f"{self.system_prompt}\n\n{memory_context}"

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

            response = await self.client.messages.create(**api_kwargs)

            # Track token usage
            self.total_input_tokens += response.usage.input_tokens
            self.total_output_tokens += response.usage.output_tokens

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
                    tool_block.input
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

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        Execute a Discord tool and return the result.

        Args:
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters

        Returns:
            String result of the tool execution
        """
        if self.bot is None:
            return "Error: Discord bot not available"

        try:
            if tool_name == "send_message":
                message = await self.bot.send_message(
                    int(tool_input["channel_id"]),
                    tool_input["content"]
                )
                return f"Message sent successfully. Message ID: {message.id}"

            elif tool_name == "edit_message":
                await self.bot.edit_message(
                    int(tool_input["channel_id"]),
                    int(tool_input["message_id"]),
                    tool_input["content"]
                )
                return f"Message {tool_input['message_id']} edited successfully"

            elif tool_name == "delete_message":
                await self.bot.delete_message(
                    int(tool_input["channel_id"]),
                    int(tool_input["message_id"])
                )
                return f"Message {tool_input['message_id']} deleted successfully"

            elif tool_name == "read_messages":
                limit = min(tool_input.get("limit", 10), 100)
                messages = await self.bot.read_messages(
                    int(tool_input["channel_id"]),
                    limit
                )
                if not messages:
                    return "No messages found in this channel"
                formatted = []
                for msg in messages:
                    timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    formatted.append(
                        f"[{timestamp}] (ID: {msg.id}) {msg.author.name}: {msg.content}"
                    )
                return "\n".join(formatted)

            elif tool_name == "list_channels":
                guild_id = tool_input.get("guild_id")
                channels = await self.bot.list_channels(
                    int(guild_id) if guild_id else None
                )
                if not channels:
                    return "No channels found"
                formatted = []
                for ch in channels:
                    guild_name = ch.guild.name if ch.guild else "Unknown"
                    formatted.append(f"[{ch.id}] #{ch.name} (in {guild_name})")
                return "\n".join(formatted)

            elif tool_name == "get_channel_info":
                info = await self.bot.get_channel_info(
                    int(tool_input["channel_id"])
                )
                return "\n".join(f"{k}: {v}" for k, v in info.items())

            elif tool_name == "describe_message_image":
                # Fetch the image from Discord
                result = await self.bot.get_message_image(
                    int(tool_input["channel_id"]),
                    int(tool_input["message_id"])
                )
                if result is None:
                    return "No image attachment found in that message."

                image_bytes, media_type = result
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

                return vision_response.content[0].text

            else:
                return f"Unknown tool: {tool_name}"

        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

    def _format_memories(
        self,
        memories: list["RetrievedMemory"],
        current_user_id: int,
        guild: Optional[discord.Guild] = None,
    ) -> str:
        """
        Format retrieved memories for injection into system prompt.

        Memories are grouped by ownership to make attribution clear:
        - User's own memories appear under "Your History"
        - Other users' public memories are grouped by their display name

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

        # Format own memories
        if own_memories:
            lines.append("\n### Your History With This User")
            for mem in own_memories:
                lines.append(f"- {mem.summary}")
                if mem.raw_dialogue:
                    # Include a brief context snippet
                    snippet = mem.raw_dialogue[:200] + "..." if len(mem.raw_dialogue) > 200 else mem.raw_dialogue
                    lines.append(f"  *Context: {snippet}*")

        # Format others' public memories
        if others_memories:
            lines.append("\n### Public Knowledge From This Server")
            for user_id, user_memories in by_user.items():
                # Resolve user_id to display name
                display_name = self._resolve_display_name(user_id, guild)
                lines.append(f"\n#### {display_name}'s shared context")
                for mem in user_memories:
                    lines.append(f"- {mem.summary}")

        lines.append("\n---")
        lines.append(
            "Use this context naturally. Attribute information correctly—"
            "don't confuse one person's facts with another's."
        )
        return "\n".join(lines)

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
            system=self.system_prompt,
            messages=[{"role": "user", "content": content}],
        )

        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        return response.content[0].text

    def clear_conversation(self, user_id: str, channel_id: str):
        """Clear conversation history for a user/channel pair."""
        key = self._get_conversation_key(user_id, channel_id)
        if key in self._conversations:
            self._conversations[key].clear()

    def get_usage_stats(self) -> dict:
        """Get token usage statistics."""
        # Pricing: $3/M input, $15/M output
        input_cost = (self.total_input_tokens / 1_000_000) * 3
        output_cost = (self.total_output_tokens / 1_000_000) * 15
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost_usd": round(input_cost + output_cost, 4),
        }
