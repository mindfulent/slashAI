# Memory System Technical Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.9.9 |
| Last Updated | 2025-12-28 |
| Status | Released |
| Author | Slash + Claude |
| References | [RMM Paper (Tan et al., 2025)](https://arxiv.org/abs/2503.08026) |
| Related Docs | [MEMORY_PRIVACY.md](./MEMORY_PRIVACY.md), [META_MEMORY_PLAN.md](./META_MEMORY_PLAN.md) |

---

## 1. Proposed File Structure

```
slashAI/
├── src/
│   ├── discord_bot.py          # Modified: passes channel to ClaudeClient
│   ├── claude_client.py        # Modified: accepts MemoryManager, channel param
│   ├── mcp_server.py           # Unchanged for v0.9.1
│   │
│   └── memory/                 # NEW: Memory subsystem
│       ├── __init__.py         # Exports MemoryManager, MemoryConfig
│       ├── config.py           # MemoryConfig dataclass
│       ├── privacy.py          # PrivacyLevel enum, classification functions
│       ├── extractor.py        # MemoryExtractor, extraction prompt
│       ├── retriever.py        # MemoryRetriever, privacy-filtered search
│       ├── updater.py          # MemoryUpdater, ADD/MERGE logic
│       └── manager.py          # MemoryManager facade
│
├── migrations/                 # NEW: Database migrations
│   ├── 001_create_memories.sql
│   ├── 002_create_sessions.sql
│   └── 003_add_indexes.sql
│
├── tests/                      # NEW: Test suite
│   └── memory/
│       ├── test_extractor.py
│       ├── test_retriever.py
│       ├── test_privacy.py     # Privacy edge case tests
│       └── test_integration.py
│
├── docs/
│   ├── ARCHITECTURE.md         # Updated with memory components
│   ├── TECHSPEC.md             # Existing
│   ├── PRD.md                  # Existing
│   ├── MEMORY_TECHSPEC.md      # This document
│   └── MEMORY_PRIVACY.md       # Privacy model details
│
├── requirements.txt            # Add: asyncpg, voyageai
├── .env.example                # Add: DATABASE_URL, VOYAGE_API_KEY
└── .do/
    └── app.yaml                # Add: DATABASE_URL, VOYAGE_API_KEY env vars
```

### 1.1 Module Responsibilities

| Module | Responsibility | Dependencies |
|--------|----------------|--------------|
| `config.py` | Configuration dataclass with defaults | None |
| `privacy.py` | Privacy level classification | discord.py |
| `extractor.py` | LLM-based topic extraction | anthropic, privacy |
| `retriever.py` | Vector search with privacy filtering | asyncpg, voyageai, privacy, config |
| `updater.py` | ADD/MERGE memory operations | asyncpg, anthropic, retriever, config |
| `manager.py` | Facade orchestrating all operations | All above |

### 1.2 Dependency Graph

```
                    ┌─────────────────┐
                    │  MemoryManager  │
                    │   (manager.py)  │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ MemoryExtractor │ │ MemoryRetriever │ │  MemoryUpdater  │
│ (extractor.py)  │ │ (retriever.py)  │ │  (updater.py)   │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         │                   │                   │
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│    privacy.py   │ │   config.py     │ │   retriever.py  │
│  PrivacyLevel   │ │  MemoryConfig   │ │   (for embed)   │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

---

## 2. Overview

### 2.1 Problem Statement

slashAI v0.9.0 stores conversation history in-memory with a 20-message FIFO buffer per `(user_id, channel_id)` pair. This approach has significant limitations:

- **Ephemeral**: All context lost on restart
- **No cross-session recall**: Cannot reference conversations from yesterday
- **No semantic retrieval**: Cannot find "that conversation about creeper farms"
- **No knowledge accumulation**: Doesn't learn persistent facts about users
- **No privacy boundaries**: No distinction between public and private contexts

### 2.2 Goals

The v0.9.1 memory system will:

1. **Persist conversations** across restarts using PostgreSQL
2. **Extract semantic topics** from conversations for efficient retrieval
3. **Enable cross-channel memories** that apply to a user globally (where appropriate)
4. **Support semantic search** via vector embeddings
5. **Maintain memory hygiene** through intelligent merging of related topics
6. **Enforce privacy boundaries** ensuring private conversations stay private

### 2.3 Non-Goals (v0.9.1)

- Reinforcement learning for retrieval optimization
- Multi-retriever architecture
- User-facing memory management commands
- GDPR-compliant data deletion workflows (deferred to v1.1.0)

### 2.4 Privacy Model Summary

See [MEMORY_PRIVACY.md](./MEMORY_PRIVACY.md) for full details. Key points:

| Privacy Level | Assigned When | Retrievable In | Cross-User |
|---------------|---------------|----------------|------------|
| `dm` | Conversation in DM | DMs only | No |
| `channel_restricted` | Role-gated channel | Same channel only | No |
| `guild_public` | Public channel | Any channel in same guild | **Yes** |
| `global` | Explicit, non-sensitive facts | Anywhere | No |

**Cross-user sharing**: `guild_public` memories are shared across all users in the same guild. When User A asks about something User B discussed publicly, they can access those memories. This enables shared guild knowledge (e.g., community members, build projects, server events).

---

## 3. Architecture

### 3.1 High-Level Design

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              slashAI v0.9.1                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐  │
│  │  Discord Bot    │───▶│  Claude Client  │───▶│  Memory Manager         │  │
│  │                 │    │                 │    │                         │  │
│  │  on_message()   │    │  chat()         │    │  - extract_topics()     │  │
│  │  _handle_chat() │    │  inject_memory()│    │  - retrieve_memories()  │  │
│  │                 │    │                 │    │  - update_memory()      │  │
│  │  [Passes channel│    │  [Passes channel│    │  - classify_privacy()   │  │
│  │   for privacy]  │    │   context]      │    │                         │  │
│  └─────────────────┘    └─────────────────┘    └───────────┬─────────────┘  │
│                                                            │                │
│  ┌─────────────────────────────────────────────────────────┼──────────────┐ │
│  │                         Data Layer                      │              │ │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌───────▼───────────┐  │ │
│  │  │  PostgreSQL     │    │  pgvector       │    │  Voyage AI        │  │ │
│  │  │  (memories w/   │◀──▶│  (semantic      │◀───│  (embeddings)     │  │ │
│  │  │  privacy_level) │    │   search)       │    │                   │  │ │
│  │  └─────────────────┘    └─────────────────┘    └───────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Why Voyage AI?

Anthropic does not offer its own embedding model. Voyage AI is Anthropic's official partner for embeddings:

| Factor | Voyage AI | OpenAI |
|--------|-----------|--------|
| Anthropic endorsed | ✅ Official partner | ❌ |
| Quality vs OpenAI-v3-large | +6-8% better retrieval | Baseline |
| Price (per 1M tokens) | $0.02 (`voyage-3.5-lite`) | $0.02 (`text-embedding-3-small`) |
| Free tier | **200M tokens** | None |
| Acquired by | MongoDB (Feb 2025) | N/A |

### 3.3 Memory Types

Based on cognitive memory models and the RMM paper:

| Type | Description | Scope | Example |
|------|-------------|-------|---------|
| **Episodic** | Specific conversations/events | Time-bound | "Last week you debugged a creeper farm" |
| **Semantic** | Learned facts about user | Persistent | "User prefers technical explanations" |
| **Procedural** | Learned preferences/behaviors | Persistent | "User likes code examples in Python" |

For v0.9.1, we'll primarily use **episodic** and **semantic**. Procedural memory is deferred.

### 3.4 Memory Scope

**Decision**: Memories are **cross-channel by default**, but **privacy-scoped**.

A fact like "User's IGN is xXSlashXx" can apply globally, but a conversation from a DM or restricted channel stays scoped to that context.

---

## 4. Database Schema

### 4.1 PostgreSQL + pgvector

```sql
-- Enable vector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Core memory table (topic-based, per RMM paper)
CREATE TABLE memories (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,                    -- Discord user ID
    
    -- Topic-based storage (from RMM paper)
    topic_summary TEXT NOT NULL,                -- Search key / human-readable summary
    raw_dialogue TEXT NOT NULL,                 -- Actual conversation to inject
    embedding vector(1024) NOT NULL,            -- For semantic search (Voyage AI dimensions)
    
    -- Classification
    memory_type TEXT NOT NULL DEFAULT 'episodic',  -- episodic | semantic | procedural
    
    -- Privacy classification (see MEMORY_PRIVACY.md)
    privacy_level TEXT NOT NULL DEFAULT 'guild_public',
    -- Valid values: 'dm' | 'channel_restricted' | 'guild_public' | 'global'
    
    -- Origin tracking (required for privacy enforcement)
    origin_channel_id BIGINT,                   -- Channel where memory was learned
    origin_guild_id BIGINT,                     -- Guild where memory was learned (NULL for DMs)
    
    -- Merge tracking
    source_count INT DEFAULT 1,                 -- How many sessions contributed
    confidence FLOAT DEFAULT 1.0,               -- For conflict resolution (0.0-1.0)
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ,               -- For future decay/relevance
    
    -- Constraints
    CONSTRAINT privacy_level_valid 
        CHECK (privacy_level IN ('dm', 'channel_restricted', 'guild_public', 'global')),
    CONSTRAINT memory_type_valid
        CHECK (memory_type IN ('episodic', 'semantic', 'procedural'))
);

-- Indexes for common queries
CREATE INDEX memories_user_id_idx ON memories(user_id);
CREATE INDEX memories_type_idx ON memories(memory_type);
CREATE INDEX memories_updated_idx ON memories(updated_at DESC);

-- Index for privacy-filtered retrieval (critical for performance)
CREATE INDEX memories_privacy_idx ON memories(user_id, privacy_level, origin_guild_id, origin_channel_id);

-- Vector similarity search index (IVFFlat for <1M rows)
CREATE INDEX memories_embedding_idx ON memories 
    USING ivfflat (embedding vector_cosine_ops) 
    WITH (lists = 100);

-- Prevent exact duplicate summaries per user
CREATE UNIQUE INDEX memories_user_summary_idx 
    ON memories(user_id, md5(topic_summary));


-- Session tracking (for extraction triggers)
CREATE TABLE sessions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,                            -- NULL for DMs
    
    -- Privacy context (captured at session start)
    channel_privacy_level TEXT NOT NULL DEFAULT 'guild_public',
    
    -- Session state
    message_count INT DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ DEFAULT NOW(),
    extracted_at TIMESTAMPTZ,                   -- NULL = not yet extracted
    
    -- Raw messages (JSONB array)
    messages JSONB DEFAULT '[]'::jsonb,
    
    UNIQUE(user_id, channel_id)
);

CREATE INDEX sessions_user_channel_idx ON sessions(user_id, channel_id);
CREATE INDEX sessions_activity_idx ON sessions(last_activity_at DESC);
```

### 4.2 Schema Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Embedding dimensions | **1024** | Voyage AI `voyage-3.5-lite` default |
| Vector index type | IVFFlat | Good for <1M rows, easy to tune |
| Summary uniqueness | MD5 hash | Prevents exact duplicates while allowing similar |
| Session storage | JSONB | Flexible, handles message structure changes |
| Privacy as column | `privacy_level` | Enables efficient filtered queries |
| Origin tracking | Required fields | Essential for privacy enforcement |

---

## 5. Configuration

```python
# src/memory/config.py

from dataclasses import dataclass

@dataclass
class MemoryConfig:
    # Retrieval settings
    top_k: int = 5                      # Number of memories to retrieve
    similarity_threshold: float = 0.3   # Minimum cosine similarity (lowered for better recall)
    
    # Extraction settings
    extraction_message_threshold: int = 10  # Extract after N messages
    extraction_inactivity_minutes: int = 30 # Extract after N minutes idle
    
    # Merge settings
    merge_similarity_threshold: float = 0.85  # Threshold for merging memories
    
    # Embedding settings (Voyage AI)
    embedding_model: str = "voyage-3.5-lite"
    embedding_dimensions: int = 1024
    
    # Token budget for injected context
    max_memory_tokens: int = 2000  # Approximate limit for injected memories
```

---

## 6. Memory Extraction

### 6.1 Extraction Triggers

Memory extraction occurs when ANY of these conditions are met:

| Trigger | Condition | Rationale |
|---------|-----------|-----------|
| Message threshold | Session reaches 10 messages | Natural conversation chunk |
| History limit | Before FIFO trim at 20 messages | Prevent data loss |
| Inactivity timeout | 30 minutes since last message | Session boundary |
| Explicit command | `/remember` (future) | User control |

### 6.2 Extraction Prompt

Adapted from RMM paper Appendix D.1.1:

```python
MEMORY_EXTRACTION_PROMPT = """
You are a memory extraction system for slashAI, a Discord bot.

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
```

### 6.3 Extraction Implementation

```python
# src/memory/extractor.py

import json
from dataclasses import dataclass
from anthropic import AsyncAnthropic
import discord

from .privacy import PrivacyLevel, classify_channel_privacy, classify_memory_privacy

@dataclass
class ExtractedMemory:
    summary: str
    memory_type: str  # "semantic" | "episodic"
    raw_dialogue: str
    confidence: float
    global_safe: bool  # Whether LLM thinks this is safe to surface globally

class MemoryExtractor:
    def __init__(self, anthropic_client: AsyncAnthropic):
        self.client = anthropic_client
    
    async def extract_with_privacy(
        self, 
        messages: list[dict],
        channel: discord.abc.Messageable,
        model: str = "claude-sonnet-4-6"
    ) -> list[tuple[ExtractedMemory, PrivacyLevel]]:
        """Extract memories and assign privacy levels based on channel context."""
        
        channel_privacy = await classify_channel_privacy(channel)
        extracted = await self._extract(messages, model)
        
        results = []
        for memory in extracted:
            privacy = classify_memory_privacy(memory, channel_privacy)
            results.append((memory, privacy))
        
        return results
    
    async def _extract(self, messages: list[dict], model: str) -> list[ExtractedMemory]:
        """Extract memorable topics from a conversation."""
        
        conversation = self._format_conversation(messages)
        if not conversation.strip():
            return []
        
        response = await self.client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": MEMORY_EXTRACTION_PROMPT.format(conversation=conversation)
            }]
        )
        
        return self._parse_response(response.content[0].text)
    
    def _format_conversation(self, messages: list[dict]) -> str:
        lines = []
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)
    
    def _parse_response(self, response_text: str) -> list[ExtractedMemory]:
        try:
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            data = json.loads(text)
            return [
                ExtractedMemory(
                    summary=item["summary"],
                    memory_type=item.get("type", "episodic"),
                    raw_dialogue=item["raw_dialogue"],
                    confidence=item.get("confidence", 1.0),
                    global_safe=item.get("global_safe", False)
                )
                for item in data.get("extracted_memories", [])
            ]
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Memory extraction parse error: {e}")
            return []
```

---

## 7. Memory Retrieval

### 7.1 Retrieval Strategy

Following the RMM paper, we use semantic search with **privacy filtering**:

1. **Classify current context** (DM, restricted channel, public channel)
2. **Embed the query** (current user message) using Voyage AI
3. **Vector search** for Top-K similar memories **with privacy filter**
4. **Inject** raw_dialogue into Claude's context

See [MEMORY_PRIVACY.md](./MEMORY_PRIVACY.md) for privacy filter SQL.

### 7.2 Retriever Implementation

```python
# src/memory/retriever.py

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import asyncpg
import voyageai
import discord

from .privacy import PrivacyLevel, classify_channel_privacy
from .config import MemoryConfig

@dataclass 
class RetrievedMemory:
    id: int
    summary: str
    raw_dialogue: str
    memory_type: str
    privacy_level: PrivacyLevel
    similarity: float
    updated_at: datetime

class MemoryRetriever:
    def __init__(
        self, 
        db_pool: asyncpg.Pool,
        config: MemoryConfig
    ):
        self.db = db_pool
        self.voyage = voyageai.AsyncClient()  # Uses VOYAGE_API_KEY env var
        self.config = config
    
    async def retrieve(
        self, 
        user_id: int, 
        query: str,
        channel: discord.abc.Messageable,
        top_k: Optional[int] = None
    ) -> list[RetrievedMemory]:
        """Retrieve relevant memories with privacy filtering."""
        
        top_k = top_k or self.config.top_k
        context_privacy = await classify_channel_privacy(channel)
        embedding = await self._embed(query, input_type="query")
        
        sql, params = self._build_privacy_query(
            user_id, embedding, context_privacy, channel, top_k
        )
        
        rows = await self.db.fetch(sql, *params)
        
        if rows:
            ids = [r["id"] for r in rows]
            await self.db.execute(
                "UPDATE memories SET last_accessed_at = NOW() WHERE id = ANY($1)",
                ids
            )
        
        return [
            RetrievedMemory(
                id=r["id"],
                summary=r["topic_summary"],
                raw_dialogue=r["raw_dialogue"],
                memory_type=r["memory_type"],
                privacy_level=PrivacyLevel(r["privacy_level"]),
                similarity=r["similarity"],
                updated_at=r["updated_at"]
            )
            for r in rows
        ]
    
    def _build_privacy_query(
        self, user_id: int, embedding: list[float],
        context_privacy: PrivacyLevel, channel: discord.abc.Messageable, top_k: int
    ) -> tuple[str, list]:
        """Build SQL query with privacy filtering.

        Privacy rules:
        - DM context: User's own memories only
        - Restricted channel: User's global/channel_restricted + ANY user's guild_public
        - Public channel: User's global + ANY user's guild_public (cross-user sharing)
        """

        base_query = """
            SELECT
                id, topic_summary, raw_dialogue, memory_type, privacy_level,
                1 - (embedding <=> $1::vector) as similarity, updated_at
            FROM memories
            WHERE 1 - (embedding <=> $1::vector) > $3
              AND ({privacy_filter})
            ORDER BY embedding <=> $1::vector
            LIMIT $4
        """

        if context_privacy == PrivacyLevel.DM:
            # DM: only user's own memories
            privacy_filter = "user_id = $2"
            params = [embedding, user_id, self.config.similarity_threshold, top_k]

        elif context_privacy == PrivacyLevel.CHANNEL_RESTRICTED:
            # Restricted: user's global + ANY user's guild_public + user's channel_restricted
            guild_id = channel.guild.id
            channel_id = channel.id
            privacy_filter = """
                (user_id = $2 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
                OR (user_id = $2 AND privacy_level = 'channel_restricted' AND origin_channel_id = $6)
            """
            params = [embedding, user_id, self.config.similarity_threshold,
                      top_k, guild_id, channel_id]

        else:  # GUILD_PUBLIC
            # Public: user's global + ANY user's guild_public (cross-user sharing)
            guild_id = channel.guild.id
            privacy_filter = """
                (user_id = $2 AND privacy_level = 'global')
                OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
            """
            params = [embedding, user_id, self.config.similarity_threshold,
                      top_k, guild_id]

        return base_query.format(privacy_filter=privacy_filter), params
    
    async def _embed(self, text: str, input_type: str = "document") -> list[float]:
        """Generate embedding using Voyage AI.
        
        Args:
            text: Text to embed
            input_type: "query" for retrieval queries, "document" for stored memories
        """
        result = await self.voyage.embed(
            [text],
            model=self.config.embedding_model,
            input_type=input_type
        )
        return result.embeddings[0]
```

---

## 8. Memory Update (ADD vs MERGE)

### 8.1 Update Logic

Per the RMM paper's "Prospective Reflection", new memories are either:

- **ADD**: If sufficiently different from existing memories
- **MERGE**: If semantically similar to an existing memory

**Privacy constraint**: Merging only occurs within the same privacy level.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Memory Update Flow                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  New Memory ──▶ Embed ──▶ Find Similar (threshold=0.85)         │
│                          (SAME privacy_level only!)             │
│                              │                                  │
│                    ┌─────────┴─────────┐                        │
│                    │                   │                        │
│               No Match              Match Found                 │
│                    │                   │                        │
│                    ▼                   ▼                        │
│               ADD new            MERGE with existing            │
│               memory             (LLM combines them)            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 Merge Prompt

```python
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
{
  "merged_summary": "...",
  "merged_dialogue": "...",
  "confidence": 0.0-1.0
}
```

OUTPUT:
"""
```

### 8.3 Updater Implementation

```python
# src/memory/updater.py

from typing import Optional
import json
import asyncpg
from anthropic import AsyncAnthropic

from .extractor import ExtractedMemory
from .retriever import MemoryRetriever
from .privacy import PrivacyLevel
from .config import MemoryConfig

class MemoryUpdater:
    def __init__(
        self, db_pool: asyncpg.Pool, retriever: MemoryRetriever,
        anthropic_client: AsyncAnthropic, config: MemoryConfig
    ):
        self.db = db_pool
        self.retriever = retriever
        self.anthropic = anthropic_client
        self.config = config
    
    async def update(
        self, user_id: int, memory: ExtractedMemory, privacy_level: PrivacyLevel,
        channel_id: Optional[int] = None, guild_id: Optional[int] = None
    ) -> int:
        """Add or merge a memory. Returns memory ID."""
        
        embedding = await self.retriever._embed(memory.summary, input_type="document")
        similar = await self._find_similar(user_id, embedding, privacy_level)
        
        if similar and similar["similarity"] > self.config.merge_similarity_threshold:
            return await self._merge(similar, memory, embedding)
        else:
            return await self._add(user_id, memory, embedding, privacy_level, 
                                   channel_id, guild_id)
    
    async def _find_similar(
        self, user_id: int, embedding: list[float], privacy_level: PrivacyLevel
    ) -> Optional[dict]:
        """Find most similar existing memory at the SAME privacy level."""
        sql = """
            SELECT id, topic_summary, raw_dialogue, source_count,
                   1 - (embedding <=> $1::vector) as similarity
            FROM memories
            WHERE user_id = $2 AND privacy_level = $3
            ORDER BY embedding <=> $1::vector
            LIMIT 1
        """
        return await self.db.fetchrow(sql, embedding, user_id, privacy_level.value)
    
    async def _merge(self, existing: dict, new: ExtractedMemory, 
                     new_embedding: list[float]) -> int:
        """Merge new memory with existing."""
        
        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": MEMORY_MERGE_PROMPT.format(
                    existing_summary=existing["topic_summary"],
                    existing_dialogue=existing["raw_dialogue"],
                    new_summary=new.summary,
                    new_dialogue=new.raw_dialogue
                )
            }]
        )
        
        merged = self._parse_merge_response(response.content[0].text)
        merged_embedding = await self.retriever._embed(merged["merged_summary"], input_type="document")
        
        result = await self.db.fetchrow(
            """
            UPDATE memories SET
                topic_summary = $1, raw_dialogue = $2, embedding = $3,
                confidence = $4, source_count = source_count + 1, updated_at = NOW()
            WHERE id = $5
            RETURNING id
            """,
            merged["merged_summary"], merged["merged_dialogue"],
            merged_embedding, merged.get("confidence", new.confidence), existing["id"]
        )
        return result["id"]
    
    async def _add(
        self, user_id: int, memory: ExtractedMemory, embedding: list[float],
        privacy_level: PrivacyLevel, channel_id: Optional[int], guild_id: Optional[int]
    ) -> int:
        """Add new memory with privacy level."""
        result = await self.db.fetchrow(
            """
            INSERT INTO memories (
                user_id, topic_summary, raw_dialogue, embedding,
                memory_type, confidence, privacy_level, origin_channel_id, origin_guild_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            user_id, memory.summary, memory.raw_dialogue, embedding,
            memory.memory_type, memory.confidence, privacy_level.value, channel_id, guild_id
        )
        return result["id"]
    
    def _parse_merge_response(self, response_text: str) -> dict:
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
```

---

## 9. Integration with ClaudeClient

### 9.1 Modified Chat Flow

```python
# src/claude_client.py (modified)

import discord
from typing import Optional
from anthropic import AsyncAnthropic

from memory.manager import MemoryManager
from memory.retriever import RetrievedMemory

class ClaudeClient:
    def __init__(
        self, api_key: str, memory_manager: Optional[MemoryManager] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT, model: str = MODEL_ID,
    ):
        self.client = AsyncAnthropic(api_key=api_key)
        self.memory = memory_manager
        self.system_prompt = system_prompt
        self.model = model
        self._conversations: dict[tuple[int, int], ConversationHistory] = {}
    
    async def chat(
        self, user_id: int, channel_id: int,
        channel: discord.abc.Messageable,  # Required for privacy
        message: str,
    ) -> str:
        """Process a chat message with privacy-aware memory augmentation."""
        
        key = (user_id, channel_id)
        if key not in self._conversations:
            self._conversations[key] = ConversationHistory()
        
        history = self._conversations[key]
        
        # Retrieve relevant memories (privacy-filtered)
        memory_context = ""
        if self.memory:
            memories = await self.memory.retrieve(user_id, message, channel)
            if memories:
                memory_context = self._format_memories(memories)
        
        history.add_message("user", message)
        
        system = self.system_prompt
        if memory_context:
            system = f"{self.system_prompt}\n\n{memory_context}"
        
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=history.get_messages()
        )
        
        assistant_message = response.content[0].text
        history.add_message("assistant", assistant_message)
        
        if self.memory:
            await self.memory.track_message(
                user_id, channel_id, channel, message, assistant_message
            )
        
        self._track_usage(response.usage)
        return assistant_message
    
    def _format_memories(self, memories: list[RetrievedMemory]) -> str:
        if not memories:
            return ""
        
        lines = ["## Relevant Context From Past Conversations"]
        for i, mem in enumerate(memories, 1):
            lines.append(f"\n### Memory {i} ({mem.memory_type})")
            lines.append(f"**Summary**: {mem.summary}")
            lines.append(f"**Context**:\n{mem.raw_dialogue}")
        
        lines.append("\n---")
        lines.append("Use this context naturally if relevant. Don't explicitly mention 'remembering' unless asked.")
        return "\n".join(lines)
```

### 9.2 Memory Manager Facade

```python
# src/memory/manager.py

import json
from typing import Optional
import asyncpg
import voyageai
import discord
from anthropic import AsyncAnthropic

from .config import MemoryConfig
from .extractor import MemoryExtractor
from .retriever import MemoryRetriever, RetrievedMemory
from .updater import MemoryUpdater
from .privacy import PrivacyLevel, classify_channel_privacy

class MemoryManager:
    """Facade for memory operations with privacy enforcement."""
    
    def __init__(
        self, db_pool: asyncpg.Pool, anthropic_client: AsyncAnthropic,
        config: Optional[MemoryConfig] = None
    ):
        self.config = config or MemoryConfig()
        self.extractor = MemoryExtractor(anthropic_client)
        self.retriever = MemoryRetriever(db_pool, self.config)
        self.updater = MemoryUpdater(db_pool, self.retriever, anthropic_client, self.config)
        self.db = db_pool
    
    async def retrieve(
        self, user_id: int, query: str, channel: discord.abc.Messageable
    ) -> list[RetrievedMemory]:
        return await self.retriever.retrieve(user_id, query, channel)
    
    async def track_message(
        self, user_id: int, channel_id: int, channel: discord.abc.Messageable,
        user_message: str, assistant_message: str
    ):
        channel_privacy = await classify_channel_privacy(channel)
        guild_id = getattr(channel, 'guild', None)
        guild_id = guild_id.id if guild_id else None
        
        session = await self._get_or_create_session(
            user_id, channel_id, guild_id, channel_privacy
        )
        
        messages = session["messages"] or []
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": assistant_message})
        
        await self.db.execute(
            """UPDATE sessions SET messages = $1, message_count = message_count + 2,
               last_activity_at = NOW() WHERE user_id = $2 AND channel_id = $3""",
            json.dumps(messages), user_id, channel_id
        )
        
        if len(messages) >= self.config.extraction_message_threshold * 2:
            await self._trigger_extraction(user_id, channel_id, channel, messages)
    
    async def _get_or_create_session(
        self, user_id: int, channel_id: int, guild_id: Optional[int], 
        channel_privacy: PrivacyLevel
    ) -> dict:
        session = await self.db.fetchrow(
            "SELECT * FROM sessions WHERE user_id = $1 AND channel_id = $2",
            user_id, channel_id
        )
        
        if not session:
            await self.db.execute(
                """INSERT INTO sessions (user_id, channel_id, guild_id, channel_privacy_level)
                   VALUES ($1, $2, $3, $4)""",
                user_id, channel_id, guild_id, channel_privacy.value
            )
            session = await self.db.fetchrow(
                "SELECT * FROM sessions WHERE user_id = $1 AND channel_id = $2",
                user_id, channel_id
            )
        
        return dict(session)
    
    async def _trigger_extraction(
        self, user_id: int, channel_id: int, 
        channel: discord.abc.Messageable, messages: list[dict]
    ):
        extracted_with_privacy = await self.extractor.extract_with_privacy(messages, channel)
        
        guild_id = getattr(channel, 'guild', None)
        guild_id = guild_id.id if guild_id else None
        
        for memory, privacy_level in extracted_with_privacy:
            await self.updater.update(user_id, memory, privacy_level, channel_id, guild_id)
        
        await self.db.execute(
            """UPDATE sessions SET extracted_at = NOW(), messages = '[]'::jsonb,
               message_count = 0 WHERE user_id = $1 AND channel_id = $2""",
            user_id, channel_id
        )
```

---

## 10. Embedding Strategy

### 10.1 Model Selection

| Model | Dimensions | Cost (per 1M tokens) | Free Tier | Recommendation |
|-------|------------|----------------------|-----------|----------------|
| `voyage-3.5-lite` | 1024 | $0.02 | **200M tokens** | **Default** ✓ |
| `voyage-3.5` | 1024 | $0.06 | 200M tokens | Higher quality |
| `voyage-3-large` | 1024 | $0.18 | 200M tokens | Best quality |
| `voyage-code-3` | 1024 | $0.18 | 200M tokens | If code-heavy |

**Why `voyage-3.5-lite`?**
- Outperforms OpenAI-v3-large by 6.34% on retrieval benchmarks
- Same price as OpenAI's cheapest model ($0.02/M tokens)
- 200M free tokens (enough for months of development + early users)
- Anthropic's official embeddings partner

### 10.2 Input Types

Voyage AI supports `input_type` hints for better retrieval:

| Input Type | Use When |
|------------|----------|
| `"query"` | Embedding user's current message for retrieval |
| `"document"` | Embedding memories being stored |

```python
# Storing a memory
embedding = await voyage.embed([memory.summary], model="voyage-3.5-lite", input_type="document")

# Retrieving memories
embedding = await voyage.embed([user_message], model="voyage-3.5-lite", input_type="query")
```

### 10.3 What to Embed

- **Summary embeddings**: Compact, semantic, good for retrieval
- **Full dialogue in storage**: Preserved for context injection

---

## 11. Cost Analysis

### 11.1 Per-User Estimates

| Operation | Frequency | Cost per Op | Monthly Cost |
|-----------|-----------|-------------|--------------|
| Extraction (Claude) | 4/week | ~$0.003 | ~$0.05 |
| Embedding (Voyage) | 10/week | ~$0.00002 | ~$0.001 |
| Retrieval (Voyage) | 40/week | ~$0.00001 | ~$0.002 |
| Merge (Claude) | 2/week | ~$0.002 | ~$0.03 |

**Total per active user**: ~$0.10/month

### 11.2 Free Tier Impact

Voyage AI provides **200M free tokens** per account. For context:

| Metric | Estimate |
|--------|----------|
| Avg tokens per embed call | ~100 tokens |
| Embeds per user per month | ~200 (50 stores + 160 retrievals) |
| Tokens per user per month | ~20,000 tokens |
| Users covered by free tier | **~10,000 users** |

You likely won't pay for Voyage AI embeddings until you have significant scale.

### 11.3 Database Costs

PostgreSQL on DigitalOcean: Basic ($15/mo) sufficient for <10K users

---

## 12. Migration Plan

### 12.1 Phase 1: Database Setup (Day 1)
1. Provision PostgreSQL database
2. Run schema migrations
3. Enable pgvector extension

### 12.2 Phase 2: Memory Infrastructure (Days 2-3)
1. Add `src/memory/` module
2. Add dependencies: `asyncpg`, `voyageai`
3. Unit test each component

### 12.3 Phase 3: Integration (Days 4-5)
1. Modify `ClaudeClient` with `channel` parameter
2. Update `DiscordBot` to pass channel context
3. Integration testing with privacy scenarios

### 12.4 Phase 4: Deployment (Day 6)
1. Update `requirements.txt` and `.do/app.yaml`
2. Deploy to staging, smoke test
3. Deploy to production

### 12.5 Rollback Plan
Set `MEMORY_ENABLED=false` to fall back to v0.9.0 behavior.

---

## 13. Future Enhancements (v1.1.0+)

- User memory commands (`/memories`, `/forget`)
- Memory decay (Ebbinghaus-inspired)
- MCP memory tools (with privacy controls)
- User privacy controls (opt-out, export)
- Channel permission sync
- **Memory introspection** - See [META_MEMORY_PLAN.md](./META_MEMORY_PLAN.md) for detailed implementation plan

---

## 14. Open Questions

1. ~~**Embedding model lock-in**: Migration risk if we switch models?~~ → Solved: Voyage AI is MongoDB-backed, stable
2. **Memory conflicts**: Track contradictions or just prefer newer?
3. **Extraction model**: Use Haiku for cost savings?
4. **Channel permission changes**: Auto-downgrade memories?
5. **Multi-guild users**: Cross-guild `global` memories?

---

## Appendix A: Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/slashai

# Embeddings (Voyage AI - Anthropic's partner)
VOYAGE_API_KEY=pa-...

# Feature flags
MEMORY_ENABLED=true
MEMORY_TOP_K=5
MEMORY_SIMILARITY_THRESHOLD=0.3
MEMORY_MERGE_THRESHOLD=0.85
```

## Appendix B: Dependencies

```txt
# requirements.txt additions for v0.9.1

# Database
asyncpg>=0.29.0

# Embeddings (Anthropic's official partner)
voyageai>=0.3.0

# Existing
discord.py>=2.3.0
mcp[cli]>=1.25.0
anthropic>=0.40.0
python-dotenv>=1.0.0
```

## Appendix C: SQL Queries

See [MEMORY_PRIVACY.md](./MEMORY_PRIVACY.md) for privacy-aware queries.

```sql
-- Find all memories for a user
SELECT topic_summary, memory_type, privacy_level, updated_at
FROM memories WHERE user_id = $1 ORDER BY updated_at DESC;

-- Memory statistics by privacy level
SELECT user_id, privacy_level, COUNT(*) as memory_count
FROM memories GROUP BY user_id, privacy_level;
```
