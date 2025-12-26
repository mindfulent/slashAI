"""
Memory Extraction

Uses Claude to extract memorable facts and topics from conversations.
Based on RMM paper methodology.
"""

import json
from dataclasses import dataclass

import discord
from anthropic import AsyncAnthropic

from .privacy import PrivacyLevel, classify_channel_privacy, classify_memory_privacy

# Extraction prompt adapted from RMM paper Appendix D.1.1
MEMORY_EXTRACTION_PROMPT = """
You are a memory extraction system for slashAI, a Discord bot serving the Minecraft College community.

## Task
Given a conversation between a User and Assistant (slashAI), extract memorable facts and topics that would be useful in future conversations.

## Output Format
Return a JSON object with the key "extracted_memories". Each memory has:
- `summary`: A concise fact or topic (1-2 sentences max)
- `type`: One of "semantic" (persistent fact) or "episodic" (conversation event)
- `raw_dialogue`: The exact conversation snippet that supports this memory
- `confidence`: 0.0-1.0 indicating certainty (1.0 = explicitly stated, 0.5 = inferred)
- `global_safe`: Whether this memory is safe to surface in ANY context (see rules below)

## What to Extract

### Semantic (persistent facts about the user):
- Minecraft-related: IGN, server preferences, favorite mods, playstyle, builds
- Personal: timezone, expertise level, technical background
- Preferences: communication style, detail level, response format

### Episodic (notable conversation events):
- Problems solved: debugging sessions, build help, mod troubleshooting
- Projects discussed: farms, bases, automation systems
- Recommendations given: mods suggested, techniques explained

## What NOT to Extract
- Generic greetings or small talk
- Information the bot provided (only extract USER information)
- Uncertain inferences (if unsure, don't include)
- Redundant information already captured in another memory

## Privacy Classification (global_safe)

Set `global_safe: true` ONLY for explicit, non-sensitive facts like:
- Minecraft IGN ("My IGN is CreeperSlayer99")
- Timezone ("I'm in PST")
- Technical preferences ("I prefer Python")
- Favorite mods/games
- Edition preference (Java vs Bedrock)

Set `global_safe: false` for EVERYTHING else, especially:
- Personal struggles, emotions, or venting
- Health, financial, or professional information
- Server-specific discussions or drama
- Moderation or admin context
- Information about OTHER users
- Anything the user might not want shared publicly
- Episodic memories (events are context-dependent)

**When in doubt, set global_safe: false.** This is the safe default.

## Example

INPUT:
```
User: hey, my creeper farm isn't working. I built the ilmango design but I'm only getting like 2 gunpowder per hour
Assistant: That's way too low. A few things to check: What's your Y level? Are you AFKing at the right distance? Any light leaks?
User: I'm at Y=200, AFKing about 130 blocks away. Let me check for light leaks... oh damn, I had a torch in the collection area
Assistant: That'll do it! Creepers won't spawn if light level is above 0 in any spawning spaces. Remove that torch and you should see rates jump to 2000+ per hour
User: fixed it, getting way better rates now. thanks! btw my IGN is CreeperSlayer99 if you see me on the server
```

OUTPUT:
```json
{
  "extracted_memories": [
    {
      "summary": "User's Minecraft IGN is CreeperSlayer99",
      "type": "semantic",
      "raw_dialogue": "User: btw my IGN is CreeperSlayer99 if you see me on the server",
      "confidence": 1.0,
      "global_safe": true
    },
    {
      "summary": "User built an ilmango creeper farm design and debugged a light leak issue",
      "type": "episodic",
      "raw_dialogue": "User: hey, my creeper farm isn't working. I built the ilmango design...\\nUser: fixed it, getting way better rates now.",
      "confidence": 1.0,
      "global_safe": false
    },
    {
      "summary": "User is familiar with technical Minecraft (knows ilmango, understands spawn mechanics)",
      "type": "semantic",
      "raw_dialogue": "User: I built the ilmango design... I'm at Y=200, AFKing about 130 blocks away",
      "confidence": 0.8,
      "global_safe": false
    }
  ]
}
```

## Your Task
Extract memories from the following conversation. If no memorable information is present, return `{"extracted_memories": []}`.

CONVERSATION:
{conversation}

OUTPUT:
"""


@dataclass
class ExtractedMemory:
    """A memory extracted from a conversation."""

    summary: str
    memory_type: str  # "semantic" | "episodic"
    raw_dialogue: str
    confidence: float
    global_safe: bool  # Whether LLM thinks this is safe to surface globally


class MemoryExtractor:
    """Extracts memorable facts from conversations using Claude."""

    def __init__(self, anthropic_client: AsyncAnthropic):
        self.client = anthropic_client

    async def extract_with_privacy(
        self,
        messages: list[dict],
        channel: discord.abc.Messageable,
        model: str = "claude-sonnet-4-5-20250929",
    ) -> list[tuple[ExtractedMemory, PrivacyLevel]]:
        """
        Extract memories and assign privacy levels based on channel context.

        Args:
            messages: List of message dicts with 'role' and 'content'
            channel: Discord channel for privacy classification
            model: Claude model to use for extraction

        Returns:
            List of (ExtractedMemory, PrivacyLevel) tuples
        """
        channel_privacy = await classify_channel_privacy(channel)
        extracted = await self._extract(messages, model)

        results = []
        for memory in extracted:
            privacy = classify_memory_privacy(memory, channel_privacy)
            results.append((memory, privacy))

        return results

    async def _extract(
        self, messages: list[dict], model: str
    ) -> list[ExtractedMemory]:
        """Extract memorable topics from a conversation."""
        conversation = self._format_conversation(messages)
        if not conversation.strip():
            return []

        response = await self.client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": MEMORY_EXTRACTION_PROMPT.format(
                        conversation=conversation
                    ),
                }
            ],
        )

        return self._parse_response(response.content[0].text)

    def _format_conversation(self, messages: list[dict]) -> str:
        """Format messages as User:/Assistant: lines."""
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def _parse_response(self, response_text: str) -> list[ExtractedMemory]:
        """Parse Claude's JSON response into ExtractedMemory objects."""
        import logging
        import re

        logger = logging.getLogger("slashAI.memory")

        try:
            text = response_text.strip()

            # Extract JSON from markdown code blocks (handles ```json or ``` with content before/after)
            code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if code_block_match:
                text = code_block_match.group(1)
            else:
                # Try to find raw JSON object
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    text = json_match.group(0)

            data = json.loads(text)
            return [
                ExtractedMemory(
                    summary=item["summary"],
                    memory_type=item.get("type", "episodic"),
                    raw_dialogue=item["raw_dialogue"],
                    confidence=item.get("confidence", 1.0),
                    global_safe=item.get("global_safe", False),
                )
                for item in data.get("extracted_memories", [])
            ]
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Memory extraction parse error: {e}")
            logger.debug(f"Raw response: {response_text[:500]}")
            return []
