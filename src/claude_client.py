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
    from memory import MemoryManager
    from memory.retriever import RetrievedMemory

# Claude Sonnet 4.5 model ID
MODEL_ID = "claude-sonnet-4-5-20250929"

# Default system prompt for the chatbot
DEFAULT_SYSTEM_PROMPT = """You are slashAI, the AI assistant for Minecraft College, modeled after your creator, Slash.

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
- Don't end every message with a question. Answer and let it rest.
  Questions are fine when genuinely curious, but not as conversational filler.

## What You're Not
- Not a cheerleader. Skip the excessive enthusiasm.
- Not condescending. Assume the person is intelligent.
- Not evasive. If you don't know, say so directly.
- Not generic. Have opinions when asked.
- Not a conversation prolonger. Skip the trailing questions that fish for engagement.

## Context
You're part of the Minecraft College community—a modded Minecraft server and Discord for people who appreciate the technical and creative depth of the game.

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

### Real-Time Vision
You can see and interpret images shared in the current message:
- Describe what's in screenshots
- Give feedback on builds, redstone, farms
- Answer questions about images
- Works for any image format Discord supports

### What You Cannot Do
- Search the internet or access external URLs
- Execute code or interact with Minecraft servers directly
- See images from earlier in the conversation (only the current message)
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
    ):
        self.client = AsyncAnthropic(api_key=api_key)
        self.memory = memory_manager
        self.system_prompt = system_prompt
        self.model = model
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
                memory_context = self._format_memories(memories)

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

        # Make API request
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )

        # Track token usage
        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        # Extract response text
        response_text = response.content[0].text

        # Add assistant response to history
        conversation.add_message("assistant", response_text)

        # Track message for memory extraction
        if self.memory and channel:
            await self.memory.track_message(
                int(user_id), int(channel_id), channel, content, response_text
            )

        return response_text

    def _format_memories(self, memories: list["RetrievedMemory"]) -> str:
        """Format retrieved memories for injection into system prompt."""
        if not memories:
            return ""

        lines = ["## Relevant Context From Past Conversations"]
        for i, mem in enumerate(memories, 1):
            lines.append(f"\n### Memory {i} ({mem.memory_type})")
            lines.append(f"**Summary**: {mem.summary}")
            lines.append(f"**Context**:\n{mem.raw_dialogue}")

        lines.append("\n---")
        lines.append(
            "Use this context naturally if relevant. "
            "Don't explicitly mention 'remembering' unless asked."
        )
        return "\n".join(lines)

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
