# Memory Management Feature Plan

**Version:** 0.9.11
**Date:** 2025-12-30
**Status:** Implemented

## Problem Statement

Users currently have no way to:
1. See what memories slashAI has stored about them
2. Delete unwanted or incorrect memories
3. Understand what information is being retained

This creates transparency and control issues for users concerned about their data.

## Requirements

### User Requirements (Confirmed via Discussion)

| Requirement | Decision |
|-------------|----------|
| Access Point | Discord commands only (no web UI) |
| Identity Linking | Discord-only (no OAuth to website) |
| Edit Capabilities | View + Delete only (no content editing) |
| Cross-user Visibility | Show others' guild_public memories about user (read-only) |

### Functional Requirements

1. **List memories** - Users can see all memories stored about them
2. **Search memories** - Users can search their memories by text
3. **View details** - Users can see full memory content (summary + raw dialogue)
4. **Delete memories** - Users can remove unwanted memories (with confirmation)
5. **See mentions** - Users can view public memories from others that reference them
6. **Statistics** - Users can see counts and breakdown of their memories

### Non-Functional Requirements

1. **Privacy** - All command responses must be ephemeral (private to invoking user)
2. **Security** - Users can ONLY delete their own memories
3. **Audit** - Deletions should be logged for debugging
4. **Pagination** - Large memory sets should be paginated (10 per page)

---

## Research Summary

### Current Memory System Architecture

**Database Schema** (from `migrations/002_create_memories.sql`):
```sql
CREATE TABLE memories (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,           -- Discord user ID
    topic_summary TEXT NOT NULL,        -- Extracted fact (pronoun-neutral)
    raw_dialogue TEXT NOT NULL,         -- Source conversation
    embedding vector(1024),             -- Voyage AI embedding
    memory_type TEXT NOT NULL,          -- 'semantic' or 'episodic'
    privacy_level TEXT NOT NULL,        -- dm/channel_restricted/guild_public/global
    origin_channel_id BIGINT NOT NULL,
    origin_guild_id BIGINT,
    source_count INT DEFAULT 1,
    confidence FLOAT DEFAULT 0.8,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ
);
```

**Key Components:**
- `src/memory/manager.py` - Facade for memory operations
- `src/memory/retriever.py` - Privacy-filtered retrieval
- `src/memory/extractor.py` - LLM-based fact extraction
- `src/memory/privacy.py` - Privacy level classification

**Existing CLI Tool** (`scripts/memory_inspector.py`):
- `list --user-id` - List memories
- `search -q` - Text search
- `inspect --memory-id` - View details
- `export` - JSON export
- `stats` - System statistics

### Identity Systems (Cross-Project Research)

**slashAI (Discord Bot):**
- Uses Discord user IDs (64-bit BIGINT) as primary identifier
- No email or user account system
- Privacy based on channel context

**minecraftcollege (Website):**
- Uses Microsoft OAuth with email-based accounts
- UUID user IDs, completely separate namespace
- No existing Discord linkage

**Conclusion:** Systems are decoupled. For Discord-only access, no identity linking is needed.

---

## Design Decisions

### Decision 1: Slash Commands vs. Message-Based Commands

**Chosen:** Slash commands (`/memories`)

**Rationale:**
- Native Discord UI with autocomplete
- Subcommand grouping (`/memories list`, `/memories delete`)
- Parameter validation built-in
- Ephemeral responses are natural
- Modern Discord UX expectations

### Decision 2: Command Structure

**Chosen:** Single command group with subcommands

```
/memories list [page] [privacy]     - List your memories (paginated)
/memories search <query> [page]     - Search your memories by text
/memories mentions [page]           - View others' public memories about you
/memories view <memory_id>          - View full memory details
/memories delete <memory_id>        - Delete a memory (with confirmation)
/memories stats                     - View your memory statistics
```

**Alternatives Considered:**
- Multiple top-level commands (`/list-memories`, `/delete-memory`) - Rejected: clutters namespace
- Single command with mode parameter (`/memories mode:list`) - Rejected: awkward UX

### Decision 3: Finding "Mentions" (Others' Memories About User)

**Chosen:** Text search for user identifiers in guild_public memories

**Approach:**
1. Get user's Discord username and display name
2. Look up user's IGN from their own global memories (if exists)
3. Search `topic_summary` and `raw_dialogue` for these identifiers
4. Filter to `guild_public` privacy + same guild + different owner

**Query:**
```sql
SELECT m.id, m.user_id, m.topic_summary, m.updated_at
FROM memories m
WHERE m.privacy_level = 'guild_public'
  AND m.origin_guild_id = $1        -- Same guild
  AND m.user_id != $2               -- Not own memories
  AND (m.topic_summary ILIKE $3 OR m.raw_dialogue ILIKE $3)
ORDER BY m.updated_at DESC;
```

**Alternatives Considered:**
- Semantic search with embedding - Rejected: overkill, text search sufficient
- Explicit tagging of mentioned users - Rejected: requires extraction prompt changes

### Decision 4: Deletion Flow

**Chosen:** ID-based deletion with button confirmation

**Flow:**
1. User runs `/memories delete <memory_id>`
2. Bot shows memory preview + Confirm/Cancel buttons
3. User clicks Confirm
4. Memory deleted, logged to audit table

**Security:**
- DELETE query includes `user_id = requesting_user` check
- Button click verifies `interaction.user.id`
- Audit log captures: memory_id, user_id, topic_summary, deleted_at

### Decision 5: UI Components

**Chosen:** Discord embeds with button navigation

**Components:**
- **Embed format** for rich memory display
- **PaginationView** - Prev/Next buttons for list navigation
- **DeleteConfirmView** - Confirm/Cancel for deletion
- All ephemeral (private to user)

---

## Implementation Plan

### Phase 1: MemoryManager Query Methods

**File:** `src/memory/manager.py`

Add these methods to MemoryManager class:

```python
async def list_user_memories(
    self, user_id: int,
    privacy_filter: Optional[str] = None,
    limit: int = 10, offset: int = 0
) -> tuple[list[dict], int]:
    """List memories for a user with pagination."""

async def search_user_memories(
    self, user_id: int, query: str,
    limit: int = 10, offset: int = 0
) -> tuple[list[dict], int]:
    """Search memories for a user by text."""

async def find_mentions(
    self, user_id: int, guild_id: int,
    identifiers: list[str],
    limit: int = 10, offset: int = 0
) -> tuple[list[dict], int]:
    """Find public memories from others mentioning this user."""

async def get_memory(self, memory_id: int) -> Optional[dict]:
    """Get a single memory by ID."""

async def delete_memory(self, memory_id: int, user_id: int) -> bool:
    """Delete a memory with ownership check."""

async def get_user_stats(self, user_id: int) -> dict:
    """Get memory statistics for a user."""
```

### Phase 2: Discord UI Components

**File:** `src/commands/views.py` (new)

```python
class PaginationView(discord.ui.View):
    """Pagination buttons for memory lists."""
    # Prev/Next buttons
    # User verification on interaction
    # 5-minute timeout

class DeleteConfirmView(discord.ui.View):
    """Confirmation dialog for memory deletion."""
    # Confirm (danger) / Cancel buttons
    # 60-second timeout
```

### Phase 3: Slash Commands Cog

**File:** `src/commands/memory_commands.py` (new)

```python
class MemoryCommands(commands.Cog):
    """Slash commands for memory management."""

    memories_group = app_commands.Group(
        name="memories",
        description="Manage your slashAI memories"
    )

    @memories_group.command(name="list")
    async def list_memories(self, interaction, page: int = 1, privacy: str = "all"):
        """List your memories."""

    @memories_group.command(name="search")
    async def search_memories(self, interaction, query: str, page: int = 1):
        """Search your memories."""

    @memories_group.command(name="mentions")
    async def view_mentions(self, interaction, page: int = 1):
        """View public memories from others that mention you."""

    @memories_group.command(name="view")
    async def view_memory(self, interaction, memory_id: int):
        """View full details of a memory."""

    @memories_group.command(name="delete")
    async def delete_memory(self, interaction, memory_id: int):
        """Delete one of your memories."""

    @memories_group.command(name="stats")
    async def memory_stats(self, interaction):
        """View your memory statistics."""
```

### Phase 4: Audit Table

**File:** `migrations/008_add_deletion_log.sql`

```sql
CREATE TABLE memory_deletion_log (
    id SERIAL PRIMARY KEY,
    memory_id INT NOT NULL,
    user_id BIGINT NOT NULL,
    topic_summary TEXT NOT NULL,
    privacy_level TEXT NOT NULL,
    deleted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX deletion_log_user_idx ON memory_deletion_log(user_id, deleted_at DESC);
```

**Rationale:** Audit logging must be in place before delete functionality goes live.

### Phase 5: Bot Integration

**File:** `src/discord_bot.py`

Add to `setup_hook` or initialization:

```python
async def setup_hook(self):
    # ... existing setup ...

    # Load memory commands cog if database is available
    if self.db_pool:
        from commands.memory_commands import MemoryCommands
        await self.add_cog(MemoryCommands(self, self.db_pool))

    # Sync commands to Discord
    await self.tree.sync()
```

### Phase 6: Testing & Documentation

1. Test all commands with various edge cases
2. Verify privacy enforcement (cannot access others' private memories)
3. Update CHANGELOG.md with new commands
4. Update CLAUDE.md with command reference

**Additional Test Cases (from review feedback):**
- User tries to view a memory ID owned by a different user
- User tries to delete a memory that was already deleted (race condition)
- Very long memory content—verify embed truncates properly without error
- User with 1000+ memories—pagination stress test

---

## Database Queries

### List User's Memories

```sql
SELECT id, topic_summary, memory_type, privacy_level,
       confidence, updated_at, last_accessed_at
FROM memories
WHERE user_id = $1
  AND ($2::text IS NULL OR privacy_level = $2)
ORDER BY updated_at DESC
LIMIT $3 OFFSET $4;
```

### Count User's Memories

```sql
SELECT COUNT(*) FROM memories
WHERE user_id = $1
  AND ($2::text IS NULL OR privacy_level = $2);
```

### Search User's Memories

```sql
SELECT id, topic_summary, memory_type, privacy_level, updated_at
FROM memories
WHERE user_id = $1
  AND (topic_summary ILIKE $2 OR raw_dialogue ILIKE $2)
ORDER BY updated_at DESC
LIMIT $3 OFFSET $4;
```

### Find Mentions

```sql
SELECT m.id, m.user_id, m.topic_summary, m.memory_type, m.updated_at
FROM memories m
WHERE m.privacy_level = 'guild_public'
  AND m.origin_guild_id = $1
  AND m.user_id != $2
  AND (m.topic_summary ILIKE $3 OR m.raw_dialogue ILIKE $3)
ORDER BY m.updated_at DESC
LIMIT $4 OFFSET $5;
```

### Get Memory by ID

```sql
SELECT id, user_id, topic_summary, raw_dialogue, memory_type,
       privacy_level, confidence, origin_guild_id, origin_channel_id,
       created_at, updated_at, last_accessed_at
FROM memories
WHERE id = $1;
```

### Delete Memory (with ownership)

```sql
DELETE FROM memories WHERE id = $1 AND user_id = $2 RETURNING id;
```

### User Statistics

```sql
SELECT
    privacy_level,
    COUNT(*) as count,
    MAX(updated_at) as last_updated
FROM memories
WHERE user_id = $1
GROUP BY privacy_level;
```

---

## Privacy & Security

### Access Control Matrix

| Action | Own Memory | Others' guild_public | Others' channel_restricted | Others' dm |
|--------|------------|---------------------|---------------------------|------------|
| View summary | Yes | Yes (if mentions) | No | No |
| View raw_dialogue | Yes | Yes (if mentions) | No | No |
| Delete | Yes | No | No | No |

### Security Safeguards

1. **Ownership Check on Delete**: DELETE query includes `user_id = $requesting_user`
2. **Ephemeral Responses**: All command outputs visible only to invoker
3. **Button Authentication**: Verify `interaction.user.id` on all button clicks
4. **Guild Context**: Mentions search limited to same guild's guild_public memories
5. **No SQL Injection**: All queries use parameterized statements
6. **Private Mentions Hidden**: If Alice and Bob have a `channel_restricted` or `dm` conversation mentioning Charlie, Charlie cannot see those memories via `/memories mentions`. Only `guild_public` memories are searchable for mentions—this is intentional privacy protection.

### Edge Cases

| Scenario | Handling |
|----------|----------|
| User tries to delete others' memory | Query returns no rows, show "Memory not found or not yours" |
| User views memory from different guild | Check `origin_guild_id`, deny if mismatch |
| Memory deleted while viewing | Show "Memory no longer exists" on action |
| User has 0 memories | Show friendly "No memories yet" message |
| Mentions search returns self-mentions | Filter `user_id != requesting_user_id` |

---

## Files Summary

### New Files

| File | Purpose | Est. Lines |
|------|---------|------------|
| `src/commands/__init__.py` | Package init | 5 |
| `src/commands/memory_commands.py` | Slash command implementations | 300 |
| `src/commands/views.py` | Discord UI components | 150 |
| `migrations/008_add_deletion_log.sql` | Audit table | 15 |

### Modified Files

| File | Changes |
|------|---------|
| `src/memory/manager.py` | Add 6 query methods (~120 lines) |
| `src/discord_bot.py` | Load cog, sync commands (~10 lines) |
| `CHANGELOG.md` | Document new commands |
| `CLAUDE.md` | Add command reference |

---

## Success Criteria

1. Users can list all their memories with pagination
2. Users can search their memories by text content
3. Users can view full details of any of their memories
4. Users can delete their own memories (with confirmation)
5. Users can see read-only view of others' public memories mentioning them
6. All privacy boundaries enforced (no access to others' private memories)
7. All command responses are ephemeral
8. Deletions are logged for audit purposes
9. Commands are documented in CHANGELOG.md

---

## Future Enhancements (Out of Scope)

- `/memories export` - Download memories as JSON
- `/memories bulk-delete` - Delete all matching a filter
- `/memories opt-out` - Disable memory system for user
- Semantic search using embeddings
- Memory age filtering (last N days)
- Web UI with Discord OAuth (if needed later)
