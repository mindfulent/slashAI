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
Memory Update (ADD vs MERGE)

Handles storing new memories with intelligent merging of similar topics.
Based on RMM paper's "Prospective Reflection" methodology.
"""

import json
from typing import Optional

import asyncpg
from anthropic import AsyncAnthropic

from .config import MemoryConfig
from .extractor import ExtractedMemory
from .privacy import PrivacyLevel
from .retriever import MemoryRetriever

# Merge prompt for combining related memories
MEMORY_MERGE_PROMPT = """
You are merging two related memories about a user into a single, consolidated memory.

## Existing Memory
Summary: {existing_summary}
Dialogue: {existing_dialogue}

## New Memory
Summary: {new_summary}
Dialogue: {new_dialogue}

## Instructions
1. Combine these into ONE memory that captures all relevant information
2. If there's a conflict, prefer the NEW memory (more recent)
3. Keep the summary concise (1-2 sentences)
4. Include relevant dialogue from both, but avoid redundancy
5. Do NOT change the privacy implications of the content

## Output Format
Return JSON:
```json
{{
  "merged_summary": "...",
  "merged_dialogue": "...",
  "confidence": 0.0-1.0
}}
```

OUTPUT:
"""


class MemoryUpdater:
    """Handles ADD and MERGE operations for memories."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        retriever: MemoryRetriever,
        anthropic_client: AsyncAnthropic,
        config: MemoryConfig,
    ):
        self.db = db_pool
        self.retriever = retriever
        self.anthropic = anthropic_client
        self.config = config

    def _embedding_to_str(self, embedding: list[float]) -> str:
        """Convert embedding list to pgvector string format."""
        return "[" + ",".join(str(x) for x in embedding) + "]"

    async def update(
        self,
        user_id: int,
        memory: ExtractedMemory,
        privacy_level: PrivacyLevel,
        channel_id: Optional[int] = None,
        guild_id: Optional[int] = None,
    ) -> int:
        """
        Add or merge a memory. Returns memory ID.

        Args:
            user_id: Discord user ID
            memory: Extracted memory to store
            privacy_level: Privacy level for the memory
            channel_id: Origin channel ID
            guild_id: Origin guild ID

        Returns:
            ID of the created or updated memory
        """
        embedding = await self.retriever._embed(memory.summary, input_type="document")
        similar = await self._find_similar(user_id, embedding, privacy_level)

        if similar and similar["similarity"] > self.config.merge_similarity_threshold:
            return await self._merge(similar, memory, embedding)
        else:
            return await self._add(
                user_id, memory, embedding, privacy_level, channel_id, guild_id
            )

    async def _find_similar(
        self, user_id: int, embedding: list[float], privacy_level: PrivacyLevel
    ) -> Optional[dict]:
        """
        Find most similar existing memory at the SAME privacy level.

        Critical: Merging only happens within the same privacy level
        to prevent privacy escalation.
        """
        sql = """
            SELECT id, topic_summary, raw_dialogue, source_count,
                   1 - (embedding <=> $1::vector) as similarity
            FROM memories
            WHERE user_id = $2 AND privacy_level = $3
            ORDER BY embedding <=> $1::vector
            LIMIT 1
        """
        embedding_str = self._embedding_to_str(embedding)
        return await self.db.fetchrow(sql, embedding_str, user_id, privacy_level.value)

    async def _merge(
        self, existing: dict, new: ExtractedMemory, new_embedding: list[float]
    ) -> int:
        """Merge new memory with existing similar memory."""
        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": MEMORY_MERGE_PROMPT.format(
                        existing_summary=existing["topic_summary"],
                        existing_dialogue=existing["raw_dialogue"],
                        new_summary=new.summary,
                        new_dialogue=new.raw_dialogue,
                    ),
                }
            ],
        )

        merged = self._parse_merge_response(response.content[0].text)

        # Re-embed the merged summary
        merged_embedding = await self.retriever._embed(
            merged["merged_summary"], input_type="document"
        )

        result = await self.db.fetchrow(
            """
            UPDATE memories SET
                topic_summary = $1, raw_dialogue = $2, embedding = $3::vector,
                confidence = $4, source_count = source_count + 1, updated_at = NOW()
            WHERE id = $5
            RETURNING id
            """,
            merged["merged_summary"],
            merged["merged_dialogue"],
            self._embedding_to_str(merged_embedding),
            merged.get("confidence", new.confidence),
            existing["id"],
        )
        return result["id"]

    async def _add(
        self,
        user_id: int,
        memory: ExtractedMemory,
        embedding: list[float],
        privacy_level: PrivacyLevel,
        channel_id: Optional[int],
        guild_id: Optional[int],
    ) -> int:
        """Add new memory with privacy level and origin tracking."""
        result = await self.db.fetchrow(
            """
            INSERT INTO memories (
                user_id, topic_summary, raw_dialogue, embedding,
                memory_type, confidence, privacy_level, origin_channel_id, origin_guild_id
            ) VALUES ($1, $2, $3, $4::vector, $5, $6, $7, $8, $9)
            ON CONFLICT (user_id, md5(topic_summary)) DO UPDATE SET
                raw_dialogue = EXCLUDED.raw_dialogue,
                embedding = EXCLUDED.embedding,
                confidence = EXCLUDED.confidence,
                updated_at = NOW(),
                source_count = memories.source_count + 1
            RETURNING id
            """,
            user_id,
            memory.summary,
            memory.raw_dialogue,
            self._embedding_to_str(embedding),
            memory.memory_type,
            memory.confidence,
            privacy_level.value,
            channel_id,
            guild_id,
        )
        return result["id"]

    def _parse_merge_response(self, response_text: str) -> dict:
        """Parse Claude's merge response JSON."""
        import re

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

        return json.loads(text)
