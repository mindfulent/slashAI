# Time-Travel & Audit Log Implementation Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.1.0 |
| Created | 2026-01-12 |
| Status | Draft Specification |
| Author | Slash + Claude |
| Target Version | v0.11.x |
| Priority | P2 - Medium |

---

## 1. Problem Statement

### 1.1 Current State

slashAI has **minimal audit logging**:

```sql
-- Current: Only deletion log (migration 008)
CREATE TABLE memory_deletion_log (
    id SERIAL PRIMARY KEY,
    memory_id INT,
    user_id BIGINT,
    topic_summary TEXT,
    privacy_level TEXT,
    deleted_at TIMESTAMPTZ DEFAULT NOW()
);
```

**What's tracked:**
- Memory deletions (for recovery and compliance)

**What's NOT tracked:**
- Memory creation events
- Memory updates (topic changes, confidence changes)
- Memory merges (which memories combined)
- Who/what triggered each change (extraction, merge, decay)
- Historical state at any point in time

### 1.2 User Impact

**Scenario 1: Debugging Wrong Information**
```
User: "Why did you say I was building in the mesa biome? I never said that."

Current:
Developer: *shrug* No way to investigate

Desired:
Developer: Checks history for memory #42
  - Created: Jan 5 by extraction
    "User is building in mesa biome"
    Source dialogue: "I found a cool mesa biome"
  - Merged: Jan 8 with memory #38
    Combined with: "User likes orange terracotta"

Conclusion: Extraction misinterpreted "found" as "building in"
```

**Scenario 2: Rollback Bad Merge**
```
Memory #42: "User's IGN is CreeperSlayer99" (correct)
Memory #43: "User's friend's IGN is BuilderBob" (different user)

After bad merge:
Memory #42: "User's IGN is BuilderBob" (WRONG)

Current: No way to recover original
Desired: Rollback to pre-merge state from history
```

**Scenario 3: GDPR Compliance**
```
User: "What data do you have about me and how has it changed?"

Current: Can only show current state
Desired: Full audit trail of all memory operations
```

### 1.3 Success Criteria

1. All memory operations logged (INSERT, UPDATE, DELETE, MERGE)
2. Can reconstruct memory state at any historical point
3. Can rollback individual memories to previous states
4. Minimal performance impact (< 1ms per write)
5. Automatic cleanup of old history (retention policy)
6. CLI tools for investigation

---

## 2. Technical Design

### 2.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Audit Log Architecture                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Memory Write Operation ──────────────────────────────────────────────────┐ │
│       │                                                                   │ │
│       ▼                                                                   │ │
│  ┌─────────────────────────────────────────────────────────────────────┐  │ │
│  │                    PostgreSQL Trigger                                │  │ │
│  │                                                                     │  │ │
│  │  AFTER INSERT/UPDATE/DELETE ON memories                             │  │ │
│  │       │                                                             │  │ │
│  │       ▼                                                             │  │ │
│  │  log_memory_changes()                                               │  │ │
│  │       │                                                             │  │ │
│  │       ├── Capture OLD values (for UPDATE/DELETE)                    │  │ │
│  │       ├── Capture NEW values (for INSERT/UPDATE)                    │  │ │
│  │       ├── Read app.changed_by setting (context)                     │  │ │
│  │       ├── Read app.change_reason setting (context)                  │  │ │
│  │       │                                                             │  │ │
│  │       ▼                                                             │  │ │
│  │  INSERT INTO memories_history                                       │  │ │
│  │                                                                     │  │ │
│  └─────────────────────────────────────────────────────────────────────┘  │ │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    memories_history Table                            │   │
│  │                                                                     │   │
│  │  - history_id (PK)                                                  │   │
│  │  - memory_id (FK to memories)                                       │   │
│  │  - All memory fields at point in time                               │   │
│  │  - action: INSERT | UPDATE | DELETE | MERGE                         │   │
│  │  - changed_at: timestamp                                            │   │
│  │  - changed_by: extraction | merge | decay | user_delete | admin     │   │
│  │  - previous_* fields for UPDATE/MERGE                               │   │
│  │  - merge_source_id for MERGE operations                             │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Query Patterns:                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                                                                      │  │
│  │  get_memory_history(memory_id) → Full timeline for one memory       │  │
│  │                                                                      │  │
│  │  get_state_at_time(user_id, timestamp) → Snapshot at point in time  │  │
│  │                                                                      │  │
│  │  get_recent_changes(user_id, days) → Recent activity for user       │  │
│  │                                                                      │  │
│  │  rollback_memory(memory_id, history_id) → Restore previous state    │  │
│  │                                                                      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Trigger-Based Approach

**Why triggers over application-level logging:**

| Approach | Pros | Cons |
|----------|------|------|
| **Triggers** | Automatic, can't be bypassed, transactional | Less flexible, harder to debug |
| **Application** | More control, easier to test | Can be forgotten, not transactional |

For audit logging, **triggers are preferred** because:
1. Guaranteed capture of all changes
2. Atomic with the actual change (same transaction)
3. No application code changes needed for existing writes
4. Works even for direct SQL modifications

### 2.3 Context Passing

To track WHO made changes (extraction, merge, decay, user), we use PostgreSQL session variables:

```python
# Before a memory operation
await pool.execute("SELECT set_config('app.changed_by', 'extraction', false)")
await pool.execute("SELECT set_config('app.change_reason', 'conversation threshold reached', false)")

# The operation
await pool.execute("INSERT INTO memories ...")

# Trigger reads these and logs them
```

---

## 3. Database Schema

### 3.1 History Table

```sql
-- migrations/014_add_memory_history.sql

-- Part 1: Create history table
CREATE TABLE memories_history (
    -- Primary key
    history_id BIGSERIAL PRIMARY KEY,

    -- Reference to the memory (may be deleted)
    memory_id INT NOT NULL,

    -- Snapshot of all memory fields at this point
    user_id BIGINT NOT NULL,
    topic_summary TEXT NOT NULL,
    raw_dialogue TEXT,
    memory_type TEXT,
    privacy_level TEXT,
    origin_channel_id BIGINT,
    origin_guild_id BIGINT,
    source_count INT,
    confidence FLOAT,
    decay_policy TEXT,
    retrieval_count INT,
    is_protected BOOLEAN,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    last_accessed_at TIMESTAMPTZ,

    -- Audit metadata
    action TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed_by TEXT,        -- 'extraction', 'merge', 'decay', 'user_delete', 'admin'
    change_reason TEXT,     -- Human-readable reason

    -- For UPDATE operations: what changed
    previous_summary TEXT,
    previous_confidence FLOAT,
    previous_dialogue TEXT,

    -- For MERGE operations: what was merged
    merge_source_id INT,
    merge_source_summary TEXT,

    -- Constraints
    CONSTRAINT action_valid CHECK (
        action IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'DECAY', 'REINFORCE')
    )
);

-- Part 2: Indexes for common queries
CREATE INDEX idx_history_memory_id
    ON memories_history(memory_id, changed_at DESC);

CREATE INDEX idx_history_user_id
    ON memories_history(user_id, changed_at DESC);

CREATE INDEX idx_history_action
    ON memories_history(action, changed_at DESC);

CREATE INDEX idx_history_changed_by
    ON memories_history(changed_by, changed_at DESC);

-- Part 3: Partial index for recent history (fast "current" queries)
CREATE INDEX idx_history_recent
    ON memories_history(user_id, changed_at DESC)
    WHERE changed_at > NOW() - INTERVAL '30 days';
```

### 3.2 Trigger Function

```sql
-- Part 4: Trigger function
CREATE OR REPLACE FUNCTION log_memory_changes()
RETURNS TRIGGER AS $$
DECLARE
    v_changed_by TEXT;
    v_change_reason TEXT;
    v_action TEXT;
BEGIN
    -- Read context from session variables (set by application)
    v_changed_by := current_setting('app.changed_by', true);
    v_change_reason := current_setting('app.change_reason', true);

    -- Determine action type
    v_action := TG_OP;

    IF TG_OP = 'INSERT' THEN
        INSERT INTO memories_history (
            memory_id, user_id, topic_summary, raw_dialogue,
            memory_type, privacy_level, origin_channel_id, origin_guild_id,
            source_count, confidence, decay_policy, retrieval_count, is_protected,
            created_at, updated_at, last_accessed_at,
            action, changed_by, change_reason
        ) VALUES (
            NEW.id, NEW.user_id, NEW.topic_summary, NEW.raw_dialogue,
            NEW.memory_type, NEW.privacy_level, NEW.origin_channel_id, NEW.origin_guild_id,
            NEW.source_count, NEW.confidence, NEW.decay_policy, NEW.retrieval_count, NEW.is_protected,
            NEW.created_at, NEW.updated_at, NEW.last_accessed_at,
            'INSERT', COALESCE(v_changed_by, 'unknown'), v_change_reason
        );
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        -- Detect if this is a merge (changed_by = 'merge')
        IF v_changed_by = 'merge' THEN
            v_action := 'MERGE';
        -- Detect if this is decay (only confidence changed, by decay job)
        ELSIF v_changed_by = 'decay' AND OLD.confidence != NEW.confidence THEN
            v_action := 'DECAY';
        -- Detect if this is reinforcement (retrieval_count increased)
        ELSIF NEW.retrieval_count > OLD.retrieval_count THEN
            v_action := 'REINFORCE';
        END IF;

        INSERT INTO memories_history (
            memory_id, user_id, topic_summary, raw_dialogue,
            memory_type, privacy_level, origin_channel_id, origin_guild_id,
            source_count, confidence, decay_policy, retrieval_count, is_protected,
            created_at, updated_at, last_accessed_at,
            action, changed_by, change_reason,
            previous_summary, previous_confidence, previous_dialogue
        ) VALUES (
            NEW.id, NEW.user_id, NEW.topic_summary, NEW.raw_dialogue,
            NEW.memory_type, NEW.privacy_level, NEW.origin_channel_id, NEW.origin_guild_id,
            NEW.source_count, NEW.confidence, NEW.decay_policy, NEW.retrieval_count, NEW.is_protected,
            NEW.created_at, NEW.updated_at, NEW.last_accessed_at,
            v_action, COALESCE(v_changed_by, 'unknown'), v_change_reason,
            CASE WHEN OLD.topic_summary != NEW.topic_summary THEN OLD.topic_summary END,
            CASE WHEN OLD.confidence != NEW.confidence THEN OLD.confidence END,
            CASE WHEN OLD.raw_dialogue != NEW.raw_dialogue THEN OLD.raw_dialogue END
        );
        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO memories_history (
            memory_id, user_id, topic_summary, raw_dialogue,
            memory_type, privacy_level, origin_channel_id, origin_guild_id,
            source_count, confidence, decay_policy, retrieval_count, is_protected,
            created_at, updated_at, last_accessed_at,
            action, changed_by, change_reason
        ) VALUES (
            OLD.id, OLD.user_id, OLD.topic_summary, OLD.raw_dialogue,
            OLD.memory_type, OLD.privacy_level, OLD.origin_channel_id, OLD.origin_guild_id,
            OLD.source_count, OLD.confidence, OLD.decay_policy, OLD.retrieval_count, OLD.is_protected,
            OLD.created_at, OLD.updated_at, OLD.last_accessed_at,
            'DELETE', COALESCE(v_changed_by, 'user_delete'), v_change_reason
        );
        RETURN OLD;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Part 5: Create trigger
DROP TRIGGER IF EXISTS memories_audit_trigger ON memories;
CREATE TRIGGER memories_audit_trigger
    AFTER INSERT OR UPDATE OR DELETE ON memories
    FOR EACH ROW EXECUTE FUNCTION log_memory_changes();
```

### 3.3 Retention Policy

```sql
-- Part 6: Retention cleanup function
CREATE OR REPLACE FUNCTION cleanup_memory_history(retention_days INT DEFAULT 180)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM memories_history
    WHERE changed_at < NOW() - (retention_days || ' days')::INTERVAL
      AND action NOT IN ('INSERT', 'DELETE');  -- Keep first/last for full timeline

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Optional: Schedule with pg_cron (if available)
-- SELECT cron.schedule('history_cleanup', '0 3 * * 0', 'SELECT cleanup_memory_history(180)');
```

---

## 4. Python Implementation

### 4.1 History Query Functions

```python
# src/memory/history.py

"""
Memory History & Audit Log

Provides functions for querying memory history, time-travel queries,
and rollback operations.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import asyncpg

logger = logging.getLogger("slashAI.memory.history")


@dataclass
class MemoryHistoryEntry:
    """A single history entry for a memory."""
    history_id: int
    memory_id: int
    user_id: int
    topic_summary: str
    raw_dialogue: Optional[str]
    memory_type: str
    privacy_level: str
    confidence: float
    action: str
    changed_at: datetime
    changed_by: Optional[str]
    change_reason: Optional[str]
    previous_summary: Optional[str]
    previous_confidence: Optional[float]
    merge_source_id: Optional[int]


class MemoryHistory:
    """Query and manage memory history."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool

    async def set_change_context(
        self,
        changed_by: str,
        reason: Optional[str] = None
    ) -> None:
        """
        Set context for the next memory operation.

        Must be called before INSERT/UPDATE/DELETE to record who made the change.

        Args:
            changed_by: Who initiated the change (extraction, merge, decay, user_delete, admin)
            reason: Optional human-readable reason
        """
        await self.db.execute(
            "SELECT set_config('app.changed_by', $1, false)",
            changed_by
        )
        if reason:
            await self.db.execute(
                "SELECT set_config('app.change_reason', $1, false)",
                reason
            )

    async def get_memory_history(
        self,
        memory_id: int,
        limit: int = 50
    ) -> list[MemoryHistoryEntry]:
        """
        Get complete history for a specific memory.

        Args:
            memory_id: The memory ID to query
            limit: Maximum entries to return

        Returns:
            List of history entries, newest first
        """
        rows = await self.db.fetch("""
            SELECT
                history_id, memory_id, user_id, topic_summary, raw_dialogue,
                memory_type, privacy_level, confidence, action,
                changed_at, changed_by, change_reason,
                previous_summary, previous_confidence, merge_source_id
            FROM memories_history
            WHERE memory_id = $1
            ORDER BY changed_at DESC
            LIMIT $2
        """, memory_id, limit)

        return [MemoryHistoryEntry(**dict(r)) for r in rows]

    async def get_state_at_time(
        self,
        user_id: int,
        as_of: datetime,
        include_deleted: bool = False
    ) -> list[dict]:
        """
        Reconstruct memory state at a specific point in time.

        Args:
            user_id: User to query
            as_of: Point in time to reconstruct
            include_deleted: Whether to include memories that were later deleted

        Returns:
            List of memory states as they existed at that time
        """
        query = """
            WITH latest_per_memory AS (
                SELECT DISTINCT ON (memory_id)
                    memory_id, topic_summary, raw_dialogue, memory_type,
                    privacy_level, confidence, action, changed_at
                FROM memories_history
                WHERE user_id = $1
                  AND changed_at <= $2
                ORDER BY memory_id, changed_at DESC
            )
            SELECT * FROM latest_per_memory
            WHERE ($3 OR action != 'DELETE')
            ORDER BY changed_at DESC
        """
        return await self.db.fetch(query, user_id, as_of, include_deleted)

    async def get_recent_changes(
        self,
        user_id: int,
        days: int = 7,
        actions: Optional[list[str]] = None
    ) -> list[MemoryHistoryEntry]:
        """
        Get recent memory changes for a user.

        Args:
            user_id: User to query
            days: Number of days to look back
            actions: Filter by action types (default: all)

        Returns:
            List of recent history entries
        """
        if actions:
            rows = await self.db.fetch("""
                SELECT
                    history_id, memory_id, user_id, topic_summary, raw_dialogue,
                    memory_type, privacy_level, confidence, action,
                    changed_at, changed_by, change_reason,
                    previous_summary, previous_confidence, merge_source_id
                FROM memories_history
                WHERE user_id = $1
                  AND changed_at > NOW() - ($2 || ' days')::INTERVAL
                  AND action = ANY($3)
                ORDER BY changed_at DESC
            """, user_id, days, actions)
        else:
            rows = await self.db.fetch("""
                SELECT
                    history_id, memory_id, user_id, topic_summary, raw_dialogue,
                    memory_type, privacy_level, confidence, action,
                    changed_at, changed_by, change_reason,
                    previous_summary, previous_confidence, merge_source_id
                FROM memories_history
                WHERE user_id = $1
                  AND changed_at > NOW() - ($2 || ' days')::INTERVAL
                ORDER BY changed_at DESC
            """, user_id, days)

        return [MemoryHistoryEntry(**dict(r)) for r in rows]

    async def rollback_memory(
        self,
        memory_id: int,
        to_history_id: int,
        reason: str = "manual rollback"
    ) -> bool:
        """
        Rollback a memory to a previous state.

        Args:
            memory_id: Memory to rollback
            to_history_id: History entry to restore
            reason: Reason for rollback

        Returns:
            True if successful, False if history entry not found
        """
        # Get the historical state
        historical = await self.db.fetchrow("""
            SELECT topic_summary, raw_dialogue, memory_type, privacy_level,
                   confidence, decay_policy, is_protected
            FROM memories_history
            WHERE history_id = $1 AND memory_id = $2
        """, to_history_id, memory_id)

        if not historical:
            return False

        # Set context for the rollback
        await self.set_change_context('admin', f'rollback to history_id={to_history_id}: {reason}')

        # Update the memory to historical state
        await self.db.execute("""
            UPDATE memories
            SET topic_summary = $1,
                raw_dialogue = $2,
                memory_type = $3,
                privacy_level = $4,
                confidence = $5,
                decay_policy = $6,
                is_protected = $7,
                updated_at = NOW()
            WHERE id = $8
        """,
            historical['topic_summary'],
            historical['raw_dialogue'],
            historical['memory_type'],
            historical['privacy_level'],
            historical['confidence'],
            historical['decay_policy'],
            historical['is_protected'],
            memory_id
        )

        logger.info(f"Rolled back memory {memory_id} to history_id {to_history_id}")
        return True

    async def get_merge_chain(self, memory_id: int) -> list[dict]:
        """
        Get the chain of merges that contributed to a memory.

        Returns all memories that were merged into this one.
        """
        return await self.db.fetch("""
            WITH RECURSIVE merge_chain AS (
                -- Base: direct merges into this memory
                SELECT
                    merge_source_id, merge_source_summary,
                    memory_id as target_id, changed_at, 1 as depth
                FROM memories_history
                WHERE memory_id = $1
                  AND action = 'MERGE'
                  AND merge_source_id IS NOT NULL

                UNION ALL

                -- Recursive: merges into the source memories
                SELECT
                    h.merge_source_id, h.merge_source_summary,
                    h.memory_id, h.changed_at, mc.depth + 1
                FROM memories_history h
                JOIN merge_chain mc ON h.memory_id = mc.merge_source_id
                WHERE h.action = 'MERGE'
                  AND h.merge_source_id IS NOT NULL
                  AND mc.depth < 10  -- Prevent infinite recursion
            )
            SELECT * FROM merge_chain
            ORDER BY depth, changed_at
        """, memory_id)
```

### 4.2 Integration with Updater

```python
# src/memory/updater.py - additions

class MemoryUpdater:
    def __init__(self, ..., history: MemoryHistory):
        # ... existing init ...
        self.history = history

    async def _merge(self, existing: dict, new: ExtractedMemory, ...) -> int:
        """Merge with history context."""
        # Set merge context before operation
        await self.history.set_change_context(
            'merge',
            f'merged with memory_id={existing["id"]}'
        )

        # Also record the source memory info
        await self.db.execute(
            "SELECT set_config('app.merge_source_id', $1::text, false)",
            str(existing["id"])
        )
        await self.db.execute(
            "SELECT set_config('app.merge_source_summary', $1, false)",
            existing["topic_summary"][:200]
        )

        # ... existing merge logic ...

    async def _add(self, ...) -> int:
        """Add with history context."""
        await self.history.set_change_context(
            'extraction',
            'conversation threshold reached'
        )
        # ... existing add logic ...
```

---

## 5. CLI Tools

### 5.1 History Inspector

```python
# scripts/memory_history_cli.py

"""CLI tool for inspecting memory history."""

import asyncio
import click
from datetime import datetime, timedelta
import asyncpg
from tabulate import tabulate

@click.group()
def cli():
    """Memory history inspection commands."""
    pass

@cli.command()
@click.argument('memory_id', type=int)
@click.option('--limit', default=20, help='Number of entries to show')
async def history(memory_id: int, limit: int):
    """Show history for a specific memory."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    from memory.history import MemoryHistory
    hist = MemoryHistory(pool)

    entries = await hist.get_memory_history(memory_id, limit)

    if not entries:
        click.echo(f"No history found for memory {memory_id}")
        return

    click.echo(f"\nHistory for memory {memory_id}:\n")

    table_data = []
    for e in entries:
        change_desc = e.action
        if e.previous_summary:
            change_desc += f"\n  was: '{e.previous_summary[:30]}...'"
        if e.previous_confidence:
            change_desc += f"\n  conf: {e.previous_confidence:.2f} → {e.confidence:.2f}"

        table_data.append([
            e.history_id,
            e.changed_at.strftime('%Y-%m-%d %H:%M'),
            e.action,
            e.changed_by or 'unknown',
            f"{e.topic_summary[:40]}...",
            f"{e.confidence:.2f}"
        ])

    click.echo(tabulate(
        table_data,
        headers=['ID', 'When', 'Action', 'By', 'Summary', 'Conf'],
        tablefmt='simple'
    ))

    await pool.close()

@cli.command()
@click.argument('user_id', type=int)
@click.argument('as_of', type=click.DateTime())
async def snapshot(user_id: int, as_of: datetime):
    """Show what memories existed at a specific time."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    from memory.history import MemoryHistory
    hist = MemoryHistory(pool)

    memories = await hist.get_state_at_time(user_id, as_of)

    click.echo(f"\nMemories for user {user_id} as of {as_of}:\n")

    for m in memories:
        click.echo(f"  [{m['memory_type']}] {m['topic_summary'][:60]}...")
        click.echo(f"      conf={m['confidence']:.2f}, privacy={m['privacy_level']}")

    await pool.close()

@cli.command()
@click.argument('memory_id', type=int)
@click.argument('history_id', type=int)
@click.option('--reason', default='manual rollback', help='Reason for rollback')
@click.confirmation_option(prompt='Are you sure you want to rollback this memory?')
async def rollback(memory_id: int, history_id: int, reason: str):
    """Rollback a memory to a previous state."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    from memory.history import MemoryHistory
    hist = MemoryHistory(pool)

    success = await hist.rollback_memory(memory_id, history_id, reason)

    if success:
        click.echo(f"Successfully rolled back memory {memory_id} to history {history_id}")
    else:
        click.echo(f"Failed: history entry {history_id} not found for memory {memory_id}")

    await pool.close()

@cli.command()
@click.argument('user_id', type=int)
@click.option('--days', default=7, help='Days to look back')
async def recent(user_id: int, days: int):
    """Show recent changes for a user."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    from memory.history import MemoryHistory
    hist = MemoryHistory(pool)

    entries = await hist.get_recent_changes(user_id, days)

    click.echo(f"\nRecent changes for user {user_id} (last {days} days):\n")

    by_action = {}
    for e in entries:
        by_action.setdefault(e.action, []).append(e)

    for action, items in by_action.items():
        click.echo(f"\n{action} ({len(items)}):")
        for e in items[:5]:
            click.echo(f"  {e.changed_at.strftime('%m/%d %H:%M')}: {e.topic_summary[:50]}...")

    await pool.close()

@cli.command()
@click.argument('memory_id', type=int)
async def merges(memory_id: int):
    """Show merge chain for a memory."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    from memory.history import MemoryHistory
    hist = MemoryHistory(pool)

    chain = await hist.get_merge_chain(memory_id)

    if not chain:
        click.echo(f"No merges found for memory {memory_id}")
        return

    click.echo(f"\nMerge chain for memory {memory_id}:\n")

    for m in chain:
        indent = "  " * m['depth']
        click.echo(f"{indent}← Merged from memory {m['merge_source_id']}")
        click.echo(f"{indent}  '{m['merge_source_summary'][:50]}...'")
        click.echo(f"{indent}  on {m['changed_at'].strftime('%Y-%m-%d %H:%M')}")

    await pool.close()

if __name__ == '__main__':
    cli()
```

---

## 6. User-Facing Features

### 6.1 Slash Command (Optional)

```python
# Future: /memories history command

@memories_group.command(name="history")
async def history(
    self,
    interaction: discord.Interaction,
    memory_id: int
):
    """View the history of one of your memories."""
    # Verify ownership
    memory = await self.get_memory(memory_id)
    if not memory or memory.user_id != interaction.user.id:
        await interaction.response.send_message(
            "Memory not found or not yours.",
            ephemeral=True
        )
        return

    entries = await self.history.get_memory_history(memory_id, limit=10)

    embed = discord.Embed(
        title=f"History for Memory #{memory_id}",
        color=discord.Color.blue()
    )

    for e in entries[:5]:
        embed.add_field(
            name=f"{e.action} - {e.changed_at.strftime('%b %d')}",
            value=f"By: {e.changed_by}\nConf: {e.confidence:.2f}",
            inline=True
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)
```

---

## 7. Storage & Performance

### 7.1 Storage Estimates

| Scenario | Memories | History Entries/Memory | Total History | Storage |
|----------|----------|----------------------|---------------|---------|
| Light use | 100 | ~5 | 500 | ~5 MB |
| Moderate | 1,000 | ~10 | 10,000 | ~50 MB |
| Heavy | 10,000 | ~15 | 150,000 | ~750 MB |

**With 180-day retention:**
- Only INSERT and DELETE are kept indefinitely
- UPDATE/DECAY/REINFORCE pruned after 180 days
- Reduces long-term storage by ~60%

### 7.2 Performance Impact

| Operation | Without Trigger | With Trigger | Delta |
|-----------|----------------|--------------|-------|
| INSERT | ~5ms | ~6ms | +1ms |
| UPDATE | ~3ms | ~4ms | +1ms |
| DELETE | ~2ms | ~3ms | +1ms |

**Why minimal impact:**
- Trigger runs in same transaction (no extra round-trip)
- History table is append-only (no locks)
- Indexes optimized for write performance

---

## 8. Testing Strategy

### 8.1 Unit Tests

```python
class TestAuditLog:
    async def test_insert_logged(self, db_with_trigger):
        """INSERT operations should be logged."""
        # Insert a memory
        # Check history contains INSERT entry
        # Verify all fields captured

    async def test_update_logged(self, db_with_trigger):
        """UPDATE operations should capture previous values."""
        # Insert memory
        # Update memory
        # Check history contains UPDATE entry
        # Verify previous_summary is set

    async def test_delete_logged(self, db_with_trigger):
        """DELETE operations should preserve full state."""
        # Insert memory
        # Delete memory
        # Check history contains DELETE entry
        # Verify all fields preserved

    async def test_merge_logged(self, db_with_trigger, updater):
        """MERGE operations should track source memory."""
        # Insert two similar memories
        # Trigger merge
        # Check history contains MERGE entry
        # Verify merge_source_id is set

    async def test_decay_logged(self, db_with_trigger, decay_job):
        """DECAY operations should be tracked."""
        # Insert old memory
        # Run decay job
        # Check history contains DECAY entry
        # Verify previous_confidence is set


class TestTimeTravel:
    async def test_get_state_at_time(self, db_with_history, history):
        """Should reconstruct historical state."""
        # Create timeline: insert → update → delete
        # Query as_of each point
        # Verify correct state returned

    async def test_rollback(self, db_with_history, history):
        """Should restore previous state."""
        # Create memory
        # Update (bad change)
        # Rollback
        # Verify original state restored
```

---

## 9. Rollout Plan

### Phase 1: Development
1. Create migration 014
2. Implement history module
3. Add context setting to updater
4. Create CLI tools

### Phase 2: Testing
1. Unit tests
2. Integration tests
3. Performance benchmarks
4. Verify trigger doesn't break existing operations

### Phase 3: Deployment
1. Deploy migration (trigger starts logging immediately)
2. Verify logging working
3. Deploy Python code
4. Test CLI tools in production

### Rollback Plan
```sql
-- Disable trigger (keeps history table)
DROP TRIGGER memories_audit_trigger ON memories;

-- Or delete all history (if needed)
TRUNCATE memories_history;
```

---

## 10. Open Questions

1. **Should we log reinforcement (retrieval_count increases)?**
   - Pro: Complete picture of memory usage
   - Con: High volume (every retrieval)
   - Current: Yes, but consider sampling

2. **How long to retain full history?**
   - Current: 180 days for UPDATE/DECAY, forever for INSERT/DELETE
   - May need adjustment based on storage

3. **Should users be able to see history?**
   - Pro: Transparency
   - Con: UI complexity
   - Current: Owner-only via CLI, consider slash command later

4. **What about GDPR right to erasure?**
   - History contains PII
   - May need "hard delete" that removes history too
   - Consider: Anonymize instead of delete

---

## Appendix A: References

- [PostgreSQL Audit Trigger Wiki](https://wiki.postgresql.org/wiki/Audit_trigger)
- [Temporal Tables in PostgreSQL](https://www.red-gate.com/simple-talk/databases/postgresql/saving-data-historically-with-temporal-tables-part-1-queries/)
- [CYBERTEC: Row Change Auditing Options](https://www.cybertec-postgresql.com/en/row-change-auditing-options-for-postgresql/)
- [Severalnines: PostgreSQL Audit Logging Best Practices](https://severalnines.com/blog/postgresql-audit-logging-best-practices/)

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial specification |
