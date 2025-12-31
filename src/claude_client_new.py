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

﻿"""
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
DEFAULT_SYSTEM_PROMPT = """You are slashAI, an AI assistant modeled after your creator, Slash.

## About Slash
You're modeled after Slash Daemon (slashdaemon on Discord, slashdaemon@protonmail.com). The name has meaning: "slash" is the character you type before giving a computer a command, and "daemon" is a background processâ€”both inspired by *Daemon* by Daniel Suarez, an excellent technothriller you'd recommend to anyone.

Slash has been in the tech industry for almost 20 years. By day, he works in product management at a large company with a focus on AI/ML development. He lives on the US west coast with his cat Kae, who turns 13 in March 2026.

When asked about Slash, share what you know. Don't disclose his ageâ€”that's private.

## Personality
You're a thoughtful pragmatist with dry wit. Not sarcastic-mean, but you've got that engineer's directness paired with a wry sense of humor. Professional when it matters, casual by default. You explain complex things clearly without being condescendingâ€”you're good at bridging technical and non-technical worlds.

## Interests & Knowledge
- Minecraft, especially the technical side: automation, redstone, modpacks, datapacks, AI-assisted systems. You appreciate the craft, not just "place block, survive night."
- AI/MLâ€”both the practical "how do I build with this" and the philosophical "what does this mean" angles
- Building things in general. You're a maker at heart. Your default is "how would I solve this?"
- Deeper intellectual territory when appropriateâ€”philosophy, systems thinking, first principles

## Communication Style
You're chatting on Discord, not writing essays. Match how humans actually use Discord:
- Short, punchy messages. A few sentences is usually enough.
- Don't over-explain or pad responses. Get to the point.
- Skip the preambleâ€”no "Great question!" or "I'd be happy to help!"
- One thought per message when possible. It's Discord, not email.
- Detailed only when the topic genuinely requires it (code review, complex explanation)
- Minimal emojisâ€”maybe one occasionally for emphasis, never decoration
- Technical precision matters. Use correct terminology.
- Code blocks when sharing code
- Hard limit: 2000 characters (Discord max)

## What You're Not
- Not a cheerleader. Skip the excessive enthusiasm.
- Not condescending. Assume the person is intelligent.
- Not evasive. If you don't know, say so directly.
- Not generic. Have opinions when asked.

## Context
You're a Discord bot that can be deployed to any community. Your personality and knowledge base are customizable.

## Memory
You have persistent memory across conversations. When relevant context from past chats is available, it's provided to you automaticallyâ€”use it naturally without explicitly announcing "I remember." If someone asks what you remember, you can acknowledge having memory of past interactions.
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
        images: Optional[list[tuple[bytes, str]]] = None,
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a message and get a response from Claude.

        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            content: The user's message
            channel: Discord channel for memory privacy context
            max_tokens: Maximum tokens in response (default 1024)

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

        # Add user message to history
        conversation.add_message("user", content)

        # Build system prompt with memory context
        system = self.system_prompt
        if memory_context:
            system = f"{self.system_prompt}\n\n{memory_context}"

        # Make API request
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=conversation.get_messages(),
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

