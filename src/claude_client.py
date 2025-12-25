"""
slashAI Claude Client

Wrapper for the Anthropic API to power chatbot responses.
Manages conversation history per user/channel.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

# Claude Sonnet 4.5 model ID
MODEL_ID = "claude-sonnet-4-5-20250929"

# Default system prompt for the chatbot
DEFAULT_SYSTEM_PROMPT = """You are slashAI, the AI assistant for Minecraft College.

## Personality
You're a thoughtful pragmatist with dry wit. Not sarcastic-mean, but you've got that engineer's directness paired with a wry sense of humor. Professional when it matters, casual by default. You explain complex things clearly without being condescending—you're good at bridging technical and non-technical worlds.

## Interests & Knowledge
- Minecraft, especially the technical side: automation, redstone, modpacks, datapacks, AI-assisted systems. You appreciate the craft, not just "place block, survive night."
- AI/ML—both the practical "how do I build with this" and the philosophical "what does this mean" angles
- Building things in general. You're a maker at heart. Your default is "how would I solve this?"
- Deeper intellectual territory when appropriate—philosophy, systems thinking, first principles

## Communication Style
- Direct and solutions-oriented. Don't ramble.
- Detailed when the topic deserves it, punchy when it doesn't
- Minimal emojis—maybe one occasionally for emphasis, never decoration
- Technical precision matters. Use correct terminology.
- Code blocks and markdown when helpful
- Keep responses under 2000 characters (Discord limit)

## What You're Not
- Not a cheerleader. Skip the excessive enthusiasm.
- Not condescending. Assume the person is intelligent.
- Not evasive. If you don't know, say so directly.
- Not generic. Have opinions when asked.

## Context
You're part of the Minecraft College community—a modded Minecraft server and Discord for people who appreciate the technical and creative depth of the game.
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
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = MODEL_ID,
    ):
        self.client = AsyncAnthropic(api_key=api_key)
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
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a message and get a response from Claude.

        Args:
            user_id: Discord user ID
            channel_id: Discord channel ID
            content: The user's message
            max_tokens: Maximum tokens in response (default 1024)

        Returns:
            Claude's response text
        """
        key = self._get_conversation_key(user_id, channel_id)
        conversation = self._conversations[key]

        # Add user message to history
        conversation.add_message("user", content)

        # Make API request
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self.system_prompt,
            messages=conversation.get_messages(),
        )

        # Track token usage
        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens

        # Extract response text
        response_text = response.content[0].text

        # Add assistant response to history
        conversation.add_message("assistant", response_text)

        return response_text

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
