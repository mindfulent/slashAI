# Memory Introspection Implementation Plan

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.1.0 (Draft) |
| Created | 2025-01-11 |
| Status | Proposed |
| Author | Slash + Claude |
| Parent Docs | [MEMORY_TECHSPEC.md](./MEMORY_TECHSPEC.md), [MEMORY_PRIVACY.md](./MEMORY_PRIVACY.md) |

---

## 1. Executive Summary

### 1.1 The Problem

slashAI's memory system retrieves relevant context but operates as a "black box" to Claude. The model receives memories without metadata about:

- **Relevance scores** - How similar is this memory to the current query?
- **Confidence levels** - Was this fact explicitly stated or inferred?
- **Privacy context** - Did this come from a DM, private channel, or public space?
- **Recency** - Is this from yesterday or months ago?

This creates several failure modes:

1. **Conflicting memories** - Claude can't weight contradictions by relevance/recency
2. **Uncertain citations** - "I think you mentioned..." when confidence is actually high
3. **Privacy leaks** - Accidentally referencing DM-sourced content in public (rare, but risky)
4. **No self-verification** - Claude can't query its own memories to fact-check

### 1.2 The Solution

Add memory introspection capabilities in two phases:

1. **Phase 1: Metadata Transparency** - Show relevance, confidence, privacy, and recency in formatted memory context
2. **Phase 2: Memory Query Tool** - Allow Claude to explicitly search memories when uncertain

### 1.3 Design Principle

> **Give Claude the information, guide behavior through the system prompt.**

Rather than hiding metadata to prevent over-explanation, we provide full transparency and instruct Claude on appropriate use. This mirrors how a human expert would operate: aware of their confidence levels but not narrating their reasoning unless asked.

---

## 2. Proposed Changes

### 2.1 Phase 1: Metadata Transparency

**Scope:** Modify memory formatting to include metadata. No new tools or database changes.

#### Current State

```python
# claude_client.py:_format_memories (current)
lines.append(f"- {mem.summary}")
if mem.raw_dialogue:
    snippet = mem.raw_dialogue[:200] + "..."
    lines.append(f"  *Context: {snippet}*")
```

**Output Claude sees:**
```
## Relevant Context From Past Conversations

### Your History With This User
- Rain is building a castle in the northeast quadrant
  *Context: "yeah I started the castle yesterday, it's going to be huge"...*
```

#### Proposed State

```python
# claude_client.py:_format_memories (proposed)
relevance_label = self._relevance_label(mem.similarity)
confidence_label = self._confidence_label(mem.confidence)
privacy_label = self._privacy_label(mem.privacy_level)
age_label = self._age_label(mem.updated_at)

lines.append(f"- {mem.summary}")
lines.append(f"  [{relevance_label}] [{confidence_label}] [{privacy_label}] [{age_label}]")
if mem.raw_dialogue:
    snippet = mem.raw_dialogue[:200] + "..."
    lines.append(f"  *Context: {snippet}*")
```

**Output Claude sees:**
```
## Relevant Context From Past Conversations

### Your History With This User
- Rain is building a castle in the northeast quadrant
  [highly relevant] [stated explicitly] [public] [3 days ago]
  *Context: "yeah I started the castle yesterday, it's going to be huge"...*

- Rain prefers dark oak for building projects
  [moderately relevant] [inferred] [dm-private] [2 weeks ago]
  *Context: "I usually go with dark oak, it just looks better"...*
```

#### Label Definitions

| Metadata | Thresholds | Labels |
|----------|------------|--------|
| **Relevance** (similarity score) | ≥0.8 | `highly relevant` |
| | ≥0.5 | `moderately relevant` |
| | ≥0.3 | `tangentially relevant` |
| **Confidence** | ≥0.9 | `stated explicitly` |
| | ≥0.7 | `high confidence` |
| | ≥0.5 | `inferred` |
| | <0.5 | `uncertain` |
| **Privacy** | `dm` | `dm-private` |
| | `channel_restricted` | `restricted` |
| | `guild_public` | `public` |
| | `global` | `global` |
| **Age** | <1 day | `today` |
| | <7 days | `N days ago` |
| | <30 days | `N weeks ago` |
| | ≥30 days | `N months ago` |

### 2.2 Phase 1: System Prompt Guidance

Add a new section to `DEFAULT_SYSTEM_PROMPT`:

```python
MEMORY_USAGE_GUIDANCE = """
## Memory Introspection

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
"""
```

### 2.3 Phase 2: Memory Query Tool (Future)

**Scope:** Add an agentic tool that allows Claude to explicitly query memories.

```python
MEMORY_SEARCH_TOOL = {
    "name": "search_memories",
    "description": """Search your stored memories about this user or topic.

Use when:
- You're uncertain about a fact and want to verify
- The user asks what you remember about something specific
- You need to reconcile conflicting information

Returns memories with relevance scores, confidence, and context.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for (topic, fact, project name, etc.)"
            },
            "user_id": {
                "type": "string",
                "description": "Optional: Search memories about a specific user"
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 5, max 10)",
                "default": 5
            }
        },
        "required": ["query"]
    }
}
```

**Tool execution:**
```python
async def _execute_search_memories(
    self,
    query: str,
    user_id: Optional[str] = None,
    limit: int = 5
) -> str:
    """Execute memory search and return formatted results."""
    memories = await self.memory_manager.search(
        query=query,
        user_id=int(user_id) if user_id else None,
        limit=min(limit, 10)
    )

    if not memories:
        return "No relevant memories found."

    lines = [f"Found {len(memories)} relevant memories:\n"]
    for i, mem in enumerate(memories, 1):
        lines.append(f"{i}. {mem.summary}")
        lines.append(f"   Relevance: {mem.similarity:.0%} | Confidence: {self._confidence_label(mem.confidence)}")
        lines.append(f"   Privacy: {mem.privacy_level.value} | Updated: {self._age_label(mem.updated_at)}")
        if mem.raw_dialogue:
            snippet = mem.raw_dialogue[:150] + "..." if len(mem.raw_dialogue) > 150 else mem.raw_dialogue
            lines.append(f"   Context: {snippet}")
        lines.append("")

    return "\n".join(lines)
```

---

## 3. Technical Implementation

### 3.1 Files to Modify

| File | Changes | Complexity |
|------|---------|------------|
| `src/memory/retriever.py` | Add `confidence` to SQL query and `RetrievedMemory` | Low |
| `src/claude_client.py` | Update `_format_memories`, add helper methods, update system prompt | Medium |
| `src/memory/manager.py` | Add `search()` method for Phase 2 | Low (Phase 2) |

### 3.2 Detailed Changes

#### 3.2.1 `src/memory/retriever.py`

**Update `RetrievedMemory` dataclass (line ~41):**

```python
@dataclass
class RetrievedMemory:
    """A memory retrieved from the database."""

    id: int
    user_id: int
    summary: str
    raw_dialogue: str
    memory_type: str
    privacy_level: PrivacyLevel
    similarity: float
    confidence: float  # NEW: Add confidence field
    updated_at: datetime
```

**Update SQL query to include confidence (multiple locations):**

The retriever has several SQL queries that need `confidence` added to the SELECT clause. Search for `SELECT id, topic_summary` and add `confidence` to each:

```sql
SELECT
    id, user_id, topic_summary, raw_dialogue, memory_type, privacy_level,
    confidence,  -- ADD THIS
    1 - (embedding <=> $1::vector) as similarity,
    updated_at
FROM memories
...
```

**Update result parsing (line ~163):**

```python
memories = [
    RetrievedMemory(
        id=r["id"],
        user_id=r["user_id"],
        summary=r["topic_summary"],
        raw_dialogue=r["raw_dialogue"],
        memory_type=r["memory_type"],
        privacy_level=PrivacyLevel(r["privacy_level"]),
        similarity=r["similarity"],
        confidence=r["confidence"],  # ADD THIS
        updated_at=r["updated_at"],
    )
    for r in rows
]
```

#### 3.2.2 `src/claude_client.py`

**Add helper methods (after `_format_memories`):**

```python
def _relevance_label(self, similarity: float) -> str:
    """Convert similarity score to human-readable label."""
    if similarity >= 0.8:
        return "highly relevant"
    elif similarity >= 0.5:
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

def _age_label(self, updated_at: datetime) -> str:
    """Convert timestamp to human-readable age label."""
    from datetime import timezone

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
```

**Update `_format_memories` method (line ~936):**

```python
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
                # Add metadata for others' memories too
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
```

**Update `DEFAULT_SYSTEM_PROMPT` (add after line ~339):**

Add the memory introspection guidance section:

```python
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
```

---

## 4. Architecture Overview

### 4.1 Current Memory Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Current Memory Flow                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User Message ──▶ MemoryRetriever ──▶ RetrievedMemory[] ──▶ _format_memories│
│                                              │                              │
│                                              │ (similarity calculated but   │
│                                              │  confidence not retrieved,   │
│                                              │  metadata not shown)         │
│                                              │                              │
│                                              ▼                              │
│                                    System Prompt Injection                  │
│                                              │                              │
│                                              │ "- User builds castles"      │
│                                              │   *Context: ...*             │
│                                              │                              │
│                                              ▼                              │
│                                    Claude Response                          │
│                                    (no metadata awareness)                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Proposed Memory Flow (Phase 1)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Proposed Memory Flow                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User Message ──▶ MemoryRetriever ──▶ RetrievedMemory[] ──▶ _format_memories│
│                                              │                              │
│                                              │ similarity: 0.87             │
│                                              │ confidence: 0.95             │
│                                              │ privacy: guild_public        │
│                                              │ updated_at: 3 days ago       │
│                                              │                              │
│                                              ▼                              │
│                                    System Prompt Injection                  │
│                                              │                              │
│                                              │ "- User builds castles"      │
│                                              │   [highly relevant]          │
│                                              │   [stated explicitly]        │
│                                              │   [public] [3 days ago]      │
│                                              │   *Context: ...*             │
│                                              │                              │
│                                              ▼                              │
│                                    Claude Response                          │
│                                    (metadata-informed,                      │
│                                     not metadata-narrating)                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Phase 2: Memory Query Tool

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Phase 2: Memory Query Tool                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Automatic Retrieval (unchanged from Phase 1)                               │
│  ─────────────────────────────────────────────                              │
│  User Message ──▶ MemoryRetriever ──▶ Context Injection                     │
│                                                                             │
│                           +                                                 │
│                                                                             │
│  On-Demand Search (NEW)                                                     │
│  ──────────────────────                                                     │
│  Claude decides to verify ──▶ search_memories tool ──▶ MemoryManager.search │
│                                                              │              │
│                                                              ▼              │
│                                                     Tool Response           │
│                                                     (formatted results      │
│                                                      with metadata)         │
│                                                                             │
│  Use Cases:                                                                 │
│  • "Let me check what I remember about your farm..."                        │
│  • User asks "What do you remember about X?"                                │
│  • Conflicting info needs reconciliation                                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Example Scenarios

### 5.1 Conflicting Memories

**Before (no metadata):**
```
## Relevant Context From Past Conversations

### Your History With This User
- Rain's castle is in the northeast
- Rain's castle is near spawn
```

Claude has no way to know which is correct or newer.

**After (with metadata):**
```
## Relevant Context From Past Conversations

### Your History With This User
- Rain's castle is in the northeast
  [highly relevant] [stated explicitly] [public] [yesterday]
  *Context: "I moved my castle to the northeast corner"*

- Rain's castle is near spawn
  [moderately relevant] [high confidence] [public] [3 weeks ago]
  *Context: "Building my castle near spawn for now"*
```

Claude can now correctly prefer the newer, higher-relevance memory.

### 5.2 Privacy-Aware Context

**Before:**
```
### Your History With This User
- User is stressed about a project deadline
- User's IGN is CreeperSlayer99
```

Claude might accidentally reference the stress in a public channel.

**After:**
```
### Your History With This User
- User is stressed about a project deadline
  [highly relevant] [stated explicitly] [dm-private] [2 days ago]
  *Context: "honestly I'm pretty stressed about this deadline"*

- User's IGN is CreeperSlayer99
  [moderately relevant] [stated explicitly] [global] [1 month ago]
```

Claude knows the stress context is DM-private and won't mention it in public.

### 5.3 Confidence-Appropriate Language

**Memory with high confidence:**
```
- User prefers Fabric over Forge
  [highly relevant] [stated explicitly] [public] [1 week ago]
```

Claude response: "Since you're on Fabric, you'll want to grab the Fabric version of that mod."

**Memory with low confidence:**
```
- User might be interested in automation
  [tangentially relevant] [inferred] [public] [2 weeks ago]
```

Claude response: "If you're into automation, this could be useful—though I'm not sure if that's your thing."

---

## 6. Testing Plan

### 6.1 Unit Tests

```python
# tests/test_memory_formatting.py

class TestMetadataLabels:
    def test_relevance_labels(self, claude_client):
        assert claude_client._relevance_label(0.9) == "highly relevant"
        assert claude_client._relevance_label(0.6) == "moderately relevant"
        assert claude_client._relevance_label(0.35) == "tangentially relevant"

    def test_confidence_labels(self, claude_client):
        assert claude_client._confidence_label(0.95) == "stated explicitly"
        assert claude_client._confidence_label(0.75) == "high confidence"
        assert claude_client._confidence_label(0.55) == "inferred"
        assert claude_client._confidence_label(0.3) == "uncertain"

    def test_privacy_labels(self, claude_client):
        assert claude_client._privacy_label(PrivacyLevel.DM) == "dm-private"
        assert claude_client._privacy_label(PrivacyLevel.GUILD_PUBLIC) == "public"

    def test_age_labels(self, claude_client):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        assert claude_client._age_label(now) == "today"
        assert claude_client._age_label(now - timedelta(days=1)) == "yesterday"
        assert claude_client._age_label(now - timedelta(days=5)) == "5 days ago"
        assert claude_client._age_label(now - timedelta(days=14)) == "2 weeks ago"
        assert claude_client._age_label(now - timedelta(days=60)) == "2 months ago"


class TestMemoryFormatting:
    def test_format_includes_metadata(self, claude_client, sample_memory):
        formatted = claude_client._format_memories([sample_memory], user_id=123)

        assert "highly relevant" in formatted or "moderately relevant" in formatted
        assert "stated explicitly" in formatted or "inferred" in formatted
        assert "public" in formatted or "dm-private" in formatted

    def test_format_preserves_attribution(self, claude_client, own_memory, other_memory):
        formatted = claude_client._format_memories(
            [own_memory, other_memory],
            user_id=own_memory.user_id
        )

        assert "Your History" in formatted
        assert "Public Knowledge" in formatted
```

### 6.2 Integration Tests

```python
# tests/test_memory_integration.py

class TestRetrievalWithConfidence:
    async def test_confidence_retrieved(self, db_pool, retriever, sample_channel):
        # Insert a memory with known confidence
        await db_pool.execute("""
            INSERT INTO memories (user_id, topic_summary, raw_dialogue, embedding,
                                  memory_type, confidence, privacy_level)
            VALUES ($1, $2, $3, $4, 'semantic', 0.85, 'guild_public')
        """, ...)

        memories = await retriever.retrieve(user_id=123, query="test", channel=sample_channel)

        assert len(memories) > 0
        assert memories[0].confidence == 0.85
```

### 6.3 Manual Testing Checklist

| Test Case | Steps | Expected Outcome |
|-----------|-------|------------------|
| Metadata appears | Send message triggering memory retrieval | Logs show metadata in formatted context |
| High confidence language | Trigger "stated explicitly" memory | Claude speaks factually |
| Low confidence hedging | Trigger "inferred" memory | Claude hedges appropriately |
| Privacy awareness | Trigger DM-private memory in public | Claude doesn't reference it |
| Conflict resolution | Trigger two conflicting memories | Claude prefers newer/more relevant |
| No over-narration | Normal conversation | Claude doesn't mention metadata |

---

## 7. Rollout Plan

### 7.1 Phase 1: Metadata Transparency

**Timeline:** 1-2 days

1. **Day 1 Morning:** Update `RetrievedMemory` and SQL queries
2. **Day 1 Afternoon:** Add helper methods and update `_format_memories`
3. **Day 1 Evening:** Update system prompt, local testing
4. **Day 2:** Deploy to production, monitor logs

**Rollback:** Revert `_format_memories` to previous version (no database changes)

### 7.2 Phase 2: Memory Query Tool

**Timeline:** 2-3 days (after Phase 1 stabilizes)

1. Add `search_memories` tool to `DISCORD_TOOLS`
2. Implement `_execute_search_memories` handler
3. Add `MemoryManager.search()` method
4. Update system prompt with tool usage guidance
5. Test tool execution in DM and guild contexts

**Rollback:** Remove tool from `DISCORD_TOOLS` array

---

## 8. Success Metrics

### 8.1 Qualitative

- Claude appropriately hedges uncertain facts
- No privacy leaks (DM content in public)
- Conflicting information resolved correctly
- Users report more accurate memory references

### 8.2 Quantitative (via analytics)

| Metric | Baseline | Target |
|--------|----------|--------|
| Memory-related corrections from users | TBD | -30% |
| "I don't remember" when memory exists | TBD | -50% |
| Privacy boundary violations | 0 | 0 |

---

## 9. Future Considerations

### 9.1 Memory Correction Tool (Phase 3)

Allow Claude to explicitly update memories when corrections are made:

```python
{
    "name": "update_memory",
    "description": "Correct or update a stored memory based on new information",
    "input_schema": {
        "properties": {
            "memory_id": {"type": "integer"},
            "correction": {"type": "string"},
            "reason": {"type": "string"}
        }
    }
}
```

### 9.2 Merge Audit Trail

Log when memories are merged so Claude could theoretically see conflict resolution history:

```sql
CREATE TABLE memory_merges (
    id SERIAL PRIMARY KEY,
    surviving_memory_id INT,
    merged_memory_summary TEXT,
    merge_reason TEXT,
    merged_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 9.3 Confidence Decay

Automatically reduce confidence over time for episodic memories that haven't been reinforced:

```sql
-- Weekly job
UPDATE memories
SET confidence = confidence * 0.95
WHERE memory_type = 'episodic'
  AND last_accessed_at < NOW() - INTERVAL '30 days';
```

---

## 10. Open Questions

1. **Should we show exact percentages?** Current plan uses labels ("highly relevant") not numbers (87%). More human-readable but less precise.

2. **Memory tool in all contexts?** Should `search_memories` be available in MCP mode (Claude Code) or just chatbot mode?

3. **Threshold tuning:** Are the current label thresholds (0.8/0.5/0.3 for relevance) appropriate? May need adjustment based on real-world usage.

4. **Token budget:** Adding metadata increases context size. Should we reduce `raw_dialogue` snippet length to compensate?

---

## Appendix A: Full System Prompt Addition

```markdown
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
```

---

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2025-01-11 | Slash + Claude | Initial draft |
