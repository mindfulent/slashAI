# Memory Privacy Model

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.9.9 |
| Last Updated | 2025-12-28 |
| Status | Released |
| Author | Slash + Claude |
| Parent Doc | [MEMORY_TECHSPEC.md](./MEMORY_TECHSPEC.md) |

---

## 1. Overview

### 1.1 Core Principle

**Only inject memories appropriate for ALL viewers of the response.**

A memory learned in a private context (DM, restricted channel) must never surface in a public context where other users could see it.

### 1.2 Why This Matters

Without privacy controls, slashAI could inadvertently:

- Expose personal information shared in DMs to public channels
- Leak moderation discussions from admin channels
- Surface server-specific context in other servers
- Violate user trust and expectations

---

## 2. Privacy Threat Scenarios

| Scenario | Risk | Example | Mitigation |
|----------|------|---------|------------|
| **DM Leakage** | User shares personal info in DM, bot references it publicly | User: "I'm stressed about exams" in DM → Bot: "How are your exams going?" in #general | `dm` privacy level - never surfaces outside DMs |
| **Admin Channel Leakage** | Mod discusses user warning, bot mentions it elsewhere | Admin: "Watching UserX for toxicity" in #mod-only → Bot references in #general | `channel_restricted` privacy level |
| **Cross-Guild Leakage** | Server A memories appear in Server B | User's Server A project details appear when chatting in Server B | `origin_guild_id` filtering |
| **Cross-User for Private** | User A's DM memories visible to User B | User A's personal struggles visible to User B | `dm` and `channel_restricted` are user-scoped |

**Note on cross-user visibility**: `guild_public` memories are intentionally shared across users in the same guild. This enables shared knowledge about community members, builds, and events. Only `dm` and `channel_restricted` memories remain strictly user-scoped.

---

## 3. Privacy Levels

### 3.1 Level Definitions

| Level | Description | Assigned When | Cross-User | Example Content |
|-------|-------------|---------------|------------|-----------------|
| `dm` | Private to user only | Conversation in DM or Group DM | No | Personal struggles, private questions |
| `channel_restricted` | Same channel only | Role-gated channel (@everyone can't view) | No | Mod discussions, admin planning |
| `guild_public` | Any channel in same guild | Public channel in a guild | **Yes** | Community members, builds, events |
| `global` | Anywhere, any server | Explicit, non-sensitive user facts | No | IGN, timezone, language preferences |

### 3.2 Retrieval Matrix

What memories can be surfaced in each context:

| Current Context | `dm` | `channel_restricted` | `guild_public` | `global` |
|-----------------|------|---------------------|----------------|----------|
| **DM** | ✅ Own only | ✅ Own only | ✅ Own only | ✅ Own only |
| **Restricted Channel** | ❌ | ✅ Own (same channel) | ✅ **Any user** (same guild) | ✅ Own only |
| **Public Channel** | ❌ | ❌ | ✅ **Any user** (same guild) | ✅ Own only |

**Key insights**:
- In a DM, the user is the only viewer, so all their own memories are safe to surface.
- `guild_public` memories are shared across all users in the same guild, enabling community knowledge.
- `global` memories are user-scoped (your IGN doesn't show up when someone else asks).

---

## 4. Privacy Classification Logic

### 4.1 Channel Detection

```python
# src/memory/privacy.py

import discord
from enum import Enum

class PrivacyLevel(str, Enum):
    DM = "dm"
    CHANNEL_RESTRICTED = "channel_restricted"
    GUILD_PUBLIC = "guild_public"
    GLOBAL = "global"


async def classify_channel_privacy(channel: discord.abc.Messageable) -> PrivacyLevel:
    """Determine privacy level based on channel type and permissions."""
    
    # DMs are always private
    if isinstance(channel, discord.DMChannel):
        return PrivacyLevel.DM
    
    if isinstance(channel, discord.GroupChannel):
        return PrivacyLevel.DM  # Group DMs treated as private
    
    # For guild channels, check if @everyone can view
    if isinstance(channel, discord.TextChannel):
        everyone_role = channel.guild.default_role
        permissions = channel.permissions_for(everyone_role)
        
        # If @everyone can't read messages, it's restricted
        if not permissions.read_messages:
            return PrivacyLevel.CHANNEL_RESTRICTED
        
        return PrivacyLevel.GUILD_PUBLIC
    
    # Default to most restrictive for unknown channel types
    return PrivacyLevel.CHANNEL_RESTRICTED
```

### 4.2 Memory Privacy Assignment

```python
def classify_memory_privacy(
    extracted_memory: 'ExtractedMemory',
    channel_privacy: PrivacyLevel
) -> PrivacyLevel:
    """
    Determine final privacy level for a memory.
    
    Some semantic facts can be promoted to 'global' if they're 
    clearly user-declared universal facts.
    """
    
    # Check if this is a global-safe fact
    if extracted_memory.global_safe and _is_global_safe(extracted_memory):
        return PrivacyLevel.GLOBAL
    
    # Otherwise, inherit channel privacy
    return channel_privacy
```

### 4.3 Global-Safe Validation

Defense-in-depth: Even if the LLM marks something as `global_safe`, we validate:

```python
def _is_global_safe(memory: 'ExtractedMemory') -> bool:
    """
    Validate that a memory marked as global_safe actually qualifies.
    """
    
    # Must be semantic (fact) not episodic (event)
    if memory.memory_type != "semantic":
        return False
    
    # Must be high confidence (explicitly stated)
    if memory.confidence < 0.9:
        return False
    
    # Check for sensitive patterns (NEVER global)
    sensitive_patterns = [
        "stressed", "anxious", "depressed", "struggling",
        "warning", "ban", "mute", "kick", "moderation",
        "salary", "income", "fired", "laid off", "job",
        "health", "sick", "diagnosis", "medication",
        "password", "secret", "private", "confidential",
        "divorce", "breakup", "relationship",
    ]
    
    summary_lower = memory.summary.lower()
    if any(pattern in summary_lower for pattern in sensitive_patterns):
        return False
    
    # Check for global-safe patterns
    global_safe_patterns = [
        "ign is", "username is", "minecraft name",
        "timezone", "time zone", "i'm in pst", "i'm in est",
        "prefers python", "prefers javascript", "prefers java",
        "codes in", "programs in", "coding language",
        "favorite mod", "favorite game", "favorite pack",
        "plays on", "java edition", "bedrock edition",
    ]
    
    return any(pattern in summary_lower for pattern in global_safe_patterns)
```

---

## 5. Privacy Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Memory Privacy Flow                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  STEP 1: Detect Channel Type                                                │
│  ───────────────────────────                                                │
│                                                                             │
│  ┌─────────────┐     ┌─────────────────────┐     ┌─────────────────────┐    │
│  │ DMChannel?  │─YES─▶│  PrivacyLevel.DM   │     │                     │    │
│  └──────┬──────┘      └─────────────────────┘     │                     │    │
│         │NO                                       │                     │    │
│         ▼                                         │                     │    │
│  ┌─────────────────┐   ┌─────────────────────┐   │                     │    │
│  │ @everyone can   │NO │ PrivacyLevel.       │   │                     │    │
│  │ read messages?  │───▶│ CHANNEL_RESTRICTED │   │                     │    │
│  └──────┬──────────┘   └─────────────────────┘   │                     │    │
│         │YES                                      │                     │    │
│         ▼                                         │                     │    │
│  ┌─────────────────────┐                          │                     │    │
│  │ PrivacyLevel.       │                          │                     │    │
│  │ GUILD_PUBLIC        │                          │                     │    │
│  └─────────────────────┘                          │                     │    │
│                                                                             │
│  STEP 2: Extract Memories (LLM)                                             │
│  ──────────────────────────────                                             │
│                                                                             │
│  LLM extracts memories and marks each with:                                 │
│  - summary, type, raw_dialogue, confidence                                  │
│  - global_safe: true/false (LLM's assessment)                               │
│                                                                             │
│  STEP 3: Assign Final Privacy Level                                         │
│  ───────────────────────────────────                                        │
│                                                                             │
│  For each extracted memory:                                                 │
│                                                                             │
│  ┌──────────────────────────────────────┐                                   │
│  │ global_safe=true AND passes          │                                   │
│  │ _is_global_safe() validation?        │                                   │
│  └──────────────┬───────────────────────┘                                   │
│                 │                                                           │
│        ┌────────┴────────┐                                                  │
│        │YES              │NO                                                │
│        ▼                 ▼                                                  │
│  ┌───────────┐    ┌─────────────────┐                                       │
│  │  GLOBAL   │    │ Inherit channel │                                       │
│  │           │    │ privacy level   │                                       │
│  └───────────┘    └─────────────────┘                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Privacy-Aware Retrieval

### 6.1 SQL Query Patterns

**DM Context (user's own memories only)**:
```sql
SELECT id, topic_summary, raw_dialogue, memory_type, privacy_level,
       1 - (embedding <=> $1::vector) as similarity
FROM memories
WHERE user_id = $2  -- Only user's own memories in DMs
  AND 1 - (embedding <=> $1::vector) > $3
ORDER BY embedding <=> $1::vector
LIMIT $4;
```

**Restricted Channel Context (cross-user for guild_public)**:
```sql
-- User's global + ANY user's guild_public + user's channel_restricted
SELECT id, topic_summary, raw_dialogue, memory_type, privacy_level,
       1 - (embedding <=> $1::vector) as similarity
FROM memories
WHERE 1 - (embedding <=> $1::vector) > $3
  AND (
    (user_id = $2 AND privacy_level = 'global')
    OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
    OR (user_id = $2 AND privacy_level = 'channel_restricted' AND origin_channel_id = $6)
  )
ORDER BY embedding <=> $1::vector
LIMIT $4;
```

**Public Channel Context (cross-user for guild_public)**:
```sql
-- User's global + ANY user's guild_public from same guild
SELECT id, topic_summary, raw_dialogue, memory_type, privacy_level,
       1 - (embedding <=> $1::vector) as similarity
FROM memories
WHERE 1 - (embedding <=> $1::vector) > $3
  AND (
    (user_id = $2 AND privacy_level = 'global')
    OR (privacy_level = 'guild_public' AND origin_guild_id = $5)
  )
ORDER BY embedding <=> $1::vector
LIMIT $4;
```

**Note**: The `guild_public` condition no longer includes `user_id`, enabling cross-user retrieval within the same guild. This allows shared knowledge about community members, builds, and events to be accessible to everyone in the guild.

### 6.2 Index for Privacy Filtering

```sql
-- Composite index for efficient privacy-filtered queries
CREATE INDEX memories_privacy_idx 
ON memories(user_id, privacy_level, origin_guild_id, origin_channel_id);
```

---

## 7. Privacy Constraints on Merging

### 7.1 Rule: Same Privacy Level Only

Memories can only be merged if they have the **same privacy level**. This prevents:

- A `dm` memory from being merged into a `global` memory (escalation)
- A `guild_public` memory from absorbing `channel_restricted` context

### 7.2 Implementation

```python
async def _find_similar(
    self, user_id: int, embedding: list[float], privacy_level: PrivacyLevel
) -> Optional[dict]:
    """Find most similar existing memory at the SAME privacy level."""
    sql = """
        SELECT id, topic_summary, raw_dialogue, source_count,
               1 - (embedding <=> $1::vector) as similarity
        FROM memories
        WHERE user_id = $2
          AND privacy_level = $3  -- CRITICAL: Same privacy level only
        ORDER BY embedding <=> $1::vector
        LIMIT 1
    """
    return await self.db.fetchrow(sql, embedding, user_id, privacy_level.value)
```

---

## 8. Edge Cases

### 8.1 Handled Scenarios

| Scenario | Privacy Level | Behavior |
|----------|---------------|----------|
| User shares IGN in DM | `global` | Surfaces everywhere (explicit, non-sensitive) |
| User vents about stress in DM | `dm` | Never surfaces outside DMs |
| Admin discusses user warning in #mod-only | `channel_restricted` | Only surfaces in #mod-only |
| User asks Minecraft question in #general | `guild_public` | Surfaces in any channel in that guild |
| User has memories from Server A, now in Server B | Only `global` | Only global memories cross guilds |

### 8.2 Edge Cases Requiring Care

| Edge Case | Risk | Current Mitigation |
|-----------|------|-------------------|
| User shares IGN in #admin channel | Could leak that channel exists | IGN is `global_safe`, promoted regardless of source |
| Two channels named #mod-only | Could cross-leak | Privacy keyed on `channel_id`, not name |
| Channel permissions change | Memory may be over/under-restricted | No auto-reclassification (conservative) |
| User explicitly says "remember globally" | Intent unclear | Not supported in v0.9.1 |

### 8.3 Conservative Defaults

The system is designed to **fail safe**:

| Situation | Default | Rationale |
|-----------|---------|-----------|
| Unknown channel type | `channel_restricted` | Most restrictive |
| `global_safe` not set by LLM | `false` | Inherit channel privacy |
| Confidence < 0.9 | Not global-safe | Only explicit facts go global |
| Sensitive keyword detected | Block global promotion | Defense in depth |

---

## 9. Global-Safe Criteria

### 9.1 What CAN Be Global

Memories can be promoted to `global` only if ALL conditions are met:

1. **Semantic type**: Must be a fact, not an episodic event
2. **High confidence**: User explicitly stated it (≥ 0.9)
3. **Non-sensitive content**: Matches safe patterns, no sensitive patterns
4. **LLM agreement**: LLM marked it as `global_safe: true`

**Safe patterns** (can be global):
- Minecraft IGN / username
- Timezone / general location (PST, EST, Europe)
- Programming language preferences
- Favorite mods / games / modpacks
- Minecraft edition (Java vs Bedrock)
- Technical expertise level

### 9.2 What CANNOT Be Global

**Sensitive patterns** (never global, regardless of LLM opinion):

| Category | Examples |
|----------|----------|
| Mental health | stressed, anxious, depressed, struggling |
| Moderation | warning, ban, mute, kick |
| Financial | salary, income, fired, laid off |
| Health | sick, diagnosis, medication |
| Security | password, secret, confidential |
| Relationships | divorce, breakup |
| Server context | drama, beef, conflict |

### 9.3 When In Doubt

**Default to NOT global.** It's better to have a user re-share information than to expose private context.

---

## 10. Testing Checklist

### 10.1 Privacy Test Cases

| Test | Input | Expected Output |
|------|-------|-----------------|
| DM memory stays in DM | Create memory in DM, query in #general | Memory NOT returned |
| DM memory appears in DM | Create memory in DM, query in DM | Memory returned |
| Restricted stays restricted | Create memory in #mod-only, query in #general | Memory NOT returned |
| Restricted appears in same channel | Create memory in #mod-only, query in #mod-only | Memory returned |
| Guild-public crosses channels | Create memory in #general, query in #help | Memory returned |
| Guild-public doesn't cross guilds | Create memory in Server A, query in Server B | Memory NOT returned |
| Global crosses guilds | Create IGN memory in Server A, query in Server B | Memory returned |
| IGN promoted to global | Share IGN in DM | Memory has `privacy_level='global'` |
| Stress NOT promoted | Share stress in DM | Memory has `privacy_level='dm'` |
| Merge within same level | Two similar `dm` memories | Merged successfully |
| Merge blocked across levels | Similar `dm` and `global` memories | NOT merged, both kept |

### 10.2 Automated Test Structure

```python
# tests/memory/test_privacy.py

import pytest
from memory.privacy import PrivacyLevel, classify_channel_privacy, classify_memory_privacy

class TestChannelClassification:
    async def test_dm_channel_is_dm(self, mock_dm_channel):
        assert await classify_channel_privacy(mock_dm_channel) == PrivacyLevel.DM
    
    async def test_public_channel_is_guild_public(self, mock_public_channel):
        assert await classify_channel_privacy(mock_public_channel) == PrivacyLevel.GUILD_PUBLIC
    
    async def test_restricted_channel_is_restricted(self, mock_restricted_channel):
        assert await classify_channel_privacy(mock_restricted_channel) == PrivacyLevel.CHANNEL_RESTRICTED

class TestMemoryClassification:
    def test_ign_is_global(self, ign_memory):
        result = classify_memory_privacy(ign_memory, PrivacyLevel.DM)
        assert result == PrivacyLevel.GLOBAL
    
    def test_stress_stays_dm(self, stress_memory):
        result = classify_memory_privacy(stress_memory, PrivacyLevel.DM)
        assert result == PrivacyLevel.DM
    
    def test_episodic_never_global(self, episodic_memory):
        episodic_memory.global_safe = True  # Even if LLM says so
        result = classify_memory_privacy(episodic_memory, PrivacyLevel.DM)
        assert result == PrivacyLevel.DM

class TestRetrievalPrivacy:
    async def test_dm_memory_not_in_public(self, db, dm_memory, public_channel):
        # ... test that DM memory doesn't appear in public channel query
    
    async def test_global_memory_everywhere(self, db, global_memory, any_channel):
        # ... test that global memory appears in any context
```

---

## 11. Future Enhancements

### 11.1 User Privacy Controls (v1.1.0)

```
/privacy status          - Show your memory privacy settings
/privacy opt-out         - Disable all memory for your account
/privacy opt-out global  - Disable global memories (keep private only)
/privacy export          - Export all your memories (GDPR)
/privacy delete          - Delete all your memories
```

### 11.2 Channel Permission Sync (v1.1.0)

- Periodic job to re-check channel permissions
- If a public channel becomes restricted: no auto-downgrade (conservative)
- If a restricted channel becomes public: flag for review
- Admin command to bulk-reclassify

### 11.3 Memory Audit Log (v1.2.0)

```sql
CREATE TABLE memory_access_log (
    id SERIAL PRIMARY KEY,
    memory_id INT REFERENCES memories(id),
    accessed_at TIMESTAMPTZ DEFAULT NOW(),
    context_type TEXT,  -- 'dm' | 'channel' | 'mcp'
    context_id BIGINT,  -- channel_id or null for MCP
    was_injected BOOLEAN
);
```

### 11.4 MCP Memory Tools (v1.1.0)

When exposing memory to Claude Code via MCP, apply strict privacy:

```python
@mcp.tool()
async def recall_user_context(user_id: str, query: str) -> str:
    """Retrieve memories about a user.
    
    Privacy: Only returns 'global' memories.
    Channel-specific and DM memories are NOT accessible via MCP.
    """
    memories = await memory_manager.retrieve_global_only(user_id, query)
    # ...
```

---

## 12. SQL Reference

### 12.1 Find All Memories With Privacy

```sql
SELECT 
    id, topic_summary, memory_type, privacy_level,
    origin_channel_id, origin_guild_id, updated_at
FROM memories
WHERE user_id = $1
ORDER BY privacy_level, updated_at DESC;
```

### 12.2 Memory Count by Privacy Level

```sql
SELECT 
    privacy_level,
    COUNT(*) as count,
    MAX(updated_at) as last_update
FROM memories
WHERE user_id = $1
GROUP BY privacy_level;
```

### 12.3 Find Potentially Misclassified Memories

```sql
-- Memories marked global that came from restricted channels
-- (might indicate classification issues)
SELECT m.id, m.topic_summary, m.privacy_level, m.origin_channel_id
FROM memories m
WHERE m.privacy_level = 'global'
  AND m.origin_channel_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM sessions s 
    WHERE s.channel_id = m.origin_channel_id 
      AND s.channel_privacy_level = 'channel_restricted'
  );
```

### 12.4 Privacy Statistics

```sql
SELECT 
    DATE_TRUNC('day', created_at) as day,
    privacy_level,
    COUNT(*) as memories_created
FROM memories
GROUP BY DATE_TRUNC('day', created_at), privacy_level
ORDER BY day DESC, privacy_level;
```
