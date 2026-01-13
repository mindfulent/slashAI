# Relevance-Weighted Confidence Decay Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 1.0.0 |
| Created | 2026-01-12 |
| Status | ✅ Implemented |
| Author | Slash + Claude |
| Implemented | v0.10.1 |
| Priority | P1 - High |

---

## 1. Problem Statement

### 1.1 Current Behavior

slashAI tracks confidence but has **no automatic decay mechanism**:

```sql
-- Current schema (migration 002)
confidence FLOAT DEFAULT 1.0,
last_accessed_at TIMESTAMPTZ,
```

**Current state:**
- Confidence is set once during extraction (1.0 for explicit, 0.5 for inferred)
- `last_accessed_at` is updated on retrieval but not used for decay
- Old memories maintain original confidence indefinitely
- No distinction between frequently-accessed and abandoned memories

### 1.2 User Impact

**Scenario 1: Stale Project Information**
```
4 months ago:
User: "I'm building an iron farm"
Memory: "User is building an iron farm" [confidence: 1.0, type: episodic]

Today:
User: "What am I working on?"

Current behavior:
Claude: "You're building an iron farm!"
(Confidently wrong - user finished that months ago)

Desired behavior:
Claude: "A few months back you mentioned building an iron farm,
but I'm not sure if you're still working on that. What are you up to now?"
(Appropriately hedged due to decay)
```

**Scenario 2: Reinforced Important Facts**
```
Week 1: User mentions their IGN
Week 2: Someone asks about their IGN (memory retrieved)
Week 3: User shares screenshots (IGN in context)
Week 4: Discussion about their builds (IGN mentioned)

Current: All memories treated equally
Desired: Frequently-accessed memory has higher confidence,
         suggesting it's important and current
```

### 1.3 Success Criteria

1. Episodic memories decay over time when not accessed
2. Semantic memories (IGN, preferences) resist decay
3. Frequently-accessed memories are reinforced
4. Claude's hedging language matches confidence levels
5. No unexpected memory loss (minimum confidence floor)
6. Background job runs reliably without performance impact

---

## 2. Technical Design

### 2.1 Memory Type Classification

| Type | Description | Decay Policy | Examples |
|------|-------------|--------------|----------|
| **Semantic** | Persistent facts about user | None (stable) | IGN, timezone, preferences |
| **Episodic** | Specific events/activities | Standard decay | Current projects, discussions |
| **Procedural** | Learned patterns | Slow decay | Play style, building preferences |

**Key insight from research:** Human memory systems distinguish between semantic (facts) and episodic (events) memory. Facts persist while episodes fade unless reinforced.

### 2.2 Decay Algorithm

**Relevance-Weighted Decay Formula:**

Decay rate varies based on how frequently the memory has been retrieved. Frequently-accessed memories are demonstrably useful and should resist decay more than rarely-accessed ones.

```
decay_resistance = min(1.0, retrieval_count / 10)
effective_decay_rate = 0.95 + (0.04 × decay_resistance)
new_confidence = confidence × (effective_decay_rate ^ periods_since_access)

Where:
  retrieval_count = number of times memory has been retrieved
  decay_resistance = 0.0 to 1.0 scale (caps at 10 retrievals)
  effective_decay_rate = 0.95 to 0.99 (1-5% reduction per period)
  periods_since_access = floor((now - last_accessed_at).days / 30)
```

**Decay Rates by Retrieval Count:**
| Retrievals | Decay Rate | Per-Period Loss |
|------------|------------|-----------------|
| 0 | 0.95 | 5% |
| 5 | 0.97 | 3% |
| 10+ | 0.99 | 1% |

**Worked Example:**
```
Memory A: "User is building an iron farm" (retrieved 2 times)
Memory B: "User's base is at -500, 64, 200" (retrieved 15 times)
Both: Initial confidence 1.0, episodic type

Day 30 (no access):
  Memory A: decay_rate = 0.95 + (0.04 × 0.2) = 0.958
            confidence = 1.0 × 0.958 = 0.958
  Memory B: decay_rate = 0.95 + (0.04 × 1.0) = 0.99
            confidence = 1.0 × 0.99 = 0.99

Day 90 (no access, 3 periods elapsed):
  Memory A: confidence = 1.0 × 0.958^3 = 0.879
  Memory B: confidence = 1.0 × 0.99^3 = 0.970

Result: The frequently-retrieved base location stays strong (0.97)
while the rarely-used project memory fades faster (0.88).
```

**Why relevance over pure time?** A memory retrieved 50 times is demonstrably useful—it should resist decay more than one retrieved twice, even if both were last accessed the same day. Pure time-based decay misses this signal.

### 2.3 Reinforcement on Access

When a memory is retrieved:
```python
new_confidence = min(0.99, confidence + REINFORCEMENT_BOOST)
retrieval_count += 1
last_accessed_at = now
```

**Reinforcement boost by memory type:**
| Type | Boost | Cap | Rationale |
|------|-------|-----|-----------|
| Semantic | +0.05 | 0.99 | Facts should stay high |
| Episodic | +0.03 | 0.95 | Events can strengthen but not become facts |
| Procedural | +0.04 | 0.97 | Patterns reinforced through use |

### 2.4 Protection Mechanisms

**Protected memories (no decay):**
1. Semantic type memories
2. Memories with `is_protected = TRUE`
3. Memories accessed within the threshold period
4. Memories with `decay_policy = 'none'`

**Minimum confidence floor:**
- Floor: 0.10 (memories never go below this)
- Rationale: Even uncertain memories may still be relevant

**Consolidation opportunity:**
- Episodic memories retrieved 5+ times may be eligible for promotion to semantic
- Logged as consolidation candidates for future review

---

## 3. Database Schema Changes

### 3.1 Migration

```sql
-- migrations/013_add_confidence_decay.sql

-- Part 1: Add decay tracking columns
ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS decay_policy TEXT DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS retrieval_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_protected BOOLEAN DEFAULT FALSE;

-- Part 2: Add constraint for valid decay policies
ALTER TABLE memories
    ADD CONSTRAINT decay_policy_valid
    CHECK (decay_policy IN ('none', 'standard', 'aggressive', 'pending_deletion'));

-- Part 3: Set default decay policies based on memory type
UPDATE memories
SET decay_policy = CASE
    WHEN memory_type = 'semantic' THEN 'none'
    WHEN memory_type = 'procedural' THEN 'standard'
    ELSE 'standard'
END
WHERE decay_policy IS NULL OR decay_policy = 'standard';

-- Part 4: Protect high-confidence semantic memories
UPDATE memories
SET is_protected = TRUE
WHERE memory_type = 'semantic'
  AND confidence >= 0.9;

-- Part 5: Initialize retrieval_count from last_accessed_at heuristic
-- (Memories that have been accessed likely have at least 1 retrieval)
UPDATE memories
SET retrieval_count = 1
WHERE last_accessed_at IS NOT NULL
  AND retrieval_count = 0;

-- Part 6: Add index for decay job queries
CREATE INDEX IF NOT EXISTS idx_memories_decay
    ON memories(memory_type, last_accessed_at, decay_policy)
    WHERE decay_policy != 'none';

-- Part 7: Add index for consolidation candidate queries
CREATE INDEX IF NOT EXISTS idx_memories_consolidation
    ON memories(memory_type, retrieval_count)
    WHERE memory_type = 'episodic' AND retrieval_count >= 5;
```

### 3.2 Schema After Migration

```sql
CREATE TABLE memories (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    topic_summary TEXT NOT NULL,
    raw_dialogue TEXT NOT NULL,
    embedding vector(1024) NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'episodic',
    privacy_level TEXT NOT NULL DEFAULT 'guild_public',
    origin_channel_id BIGINT,
    origin_guild_id BIGINT,
    source_count INT DEFAULT 1,
    confidence FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ,

    -- NEW: Decay tracking
    decay_policy TEXT DEFAULT 'standard',
    retrieval_count INT DEFAULT 0,
    is_protected BOOLEAN DEFAULT FALSE,

    -- Constraints
    CONSTRAINT privacy_level_valid CHECK (...),
    CONSTRAINT memory_type_valid CHECK (...),
    CONSTRAINT decay_policy_valid CHECK (
        decay_policy IN ('none', 'standard', 'aggressive', 'pending_deletion')
    )
);
```

---

## 4. Python Implementation

### 4.1 Decay Job Module

```python
# src/memory/decay.py

"""
Memory Confidence Decay

Background job that applies relevance-weighted decay to episodic memories
and identifies consolidation candidates.

Decay policy:
- Episodic memories decay based on retrieval frequency AND time since access
- High retrieval_count (10+) = slow decay (1% per period)
- Low retrieval_count (0) = fast decay (5% per period)
- Semantic memories do not decay
- Frequently-accessed memories are reinforced on each retrieval
- Very low confidence memories are flagged for potential cleanup
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
from discord.ext import tasks

logger = logging.getLogger("slashAI.memory.decay")

# Decay configuration
BASE_DECAY_RATE = 0.95     # Decay rate for memories with 0 retrievals (5% per period)
MAX_DECAY_RATE = 0.99      # Decay rate for memories with 10+ retrievals (1% per period)
DECAY_PERIOD_DAYS = 30     # Apply decay after this many days without access
MIN_CONFIDENCE = 0.10      # Floor - memories never drop below this
CLEANUP_THRESHOLD = 0.10   # Mark for potential cleanup below this
CLEANUP_AGE_DAYS = 90      # Only cleanup old memories
CONSOLIDATION_THRESHOLD = 5  # Retrieval count for consolidation candidate


class MemoryDecayJob:
    """Background job for memory confidence decay."""

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool
        self._started = False

    def start(self) -> None:
        """Start the decay job loop."""
        if not self._started:
            self._decay_loop.start()
            self._started = True
            logger.info("Memory decay job started (runs every 6 hours)")

    def stop(self) -> None:
        """Stop the decay job loop."""
        if self._started:
            self._decay_loop.cancel()
            self._started = False
            logger.info("Memory decay job stopped")

    @tasks.loop(hours=6)
    async def _decay_loop(self) -> None:
        """Run decay operations every 6 hours."""
        try:
            await self.run_decay()
        except Exception as e:
            logger.error(f"Error in decay job: {e}", exc_info=True)

    async def run_decay(self) -> dict:
        """
        Execute the decay job.

        Returns:
            Statistics about the decay run
        """
        stats = {
            "decayed_count": 0,
            "cleanup_flagged": 0,
            "consolidation_candidates": 0,
        }

        # Step 1: Apply exponential decay to episodic memories
        stats["decayed_count"] = await self._apply_decay()

        # Step 2: Flag very low confidence memories for cleanup
        stats["cleanup_flagged"] = await self._flag_for_cleanup()

        # Step 3: Identify consolidation candidates
        stats["consolidation_candidates"] = await self._find_consolidation_candidates()

        logger.info(
            f"Decay job complete: decayed={stats['decayed_count']}, "
            f"cleanup_flagged={stats['cleanup_flagged']}, "
            f"consolidation_candidates={stats['consolidation_candidates']}"
        )

        return stats

    async def _apply_decay(self) -> int:
        """
        Apply relevance-weighted decay to eligible memories.

        Formula: new_confidence = confidence * (effective_rate ^ periods_elapsed)
        Where:
          effective_rate = 0.95 + (0.04 * min(1.0, retrieval_count / 10))
          periods_elapsed = floor(days_since_access / 30)

        Memories with higher retrieval_count decay slower (0.99 vs 0.95).
        """
        result = await self.db.execute("""
            UPDATE memories
            SET confidence = GREATEST(
                $1,
                confidence * POWER(
                    -- Relevance-weighted decay rate: 0.95 to 0.99 based on retrieval_count
                    $2 + (($3 - $2) * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10)),
                    FLOOR(EXTRACT(EPOCH FROM (NOW() - last_accessed_at)) / 86400 / $4)
                )
            ),
            updated_at = NOW()
            WHERE decay_policy = 'standard'
              AND is_protected = FALSE
              AND last_accessed_at IS NOT NULL
              AND last_accessed_at < NOW() - INTERVAL '%s days'
              AND confidence > $1
        """ % DECAY_PERIOD_DAYS, MIN_CONFIDENCE, BASE_DECAY_RATE, MAX_DECAY_RATE, DECAY_PERIOD_DAYS)

        # Parse affected row count from result
        count = int(result.split()[-1]) if result else 0
        return count

    async def _flag_for_cleanup(self) -> int:
        """Flag very low confidence old memories for potential cleanup."""
        result = await self.db.execute("""
            UPDATE memories
            SET decay_policy = 'pending_deletion'
            WHERE decay_policy = 'standard'
              AND is_protected = FALSE
              AND confidence < $1
              AND created_at < NOW() - INTERVAL '%s days'
        """ % CLEANUP_AGE_DAYS, CLEANUP_THRESHOLD)

        count = int(result.split()[-1]) if result else 0
        return count

    async def _find_consolidation_candidates(self) -> int:
        """
        Find episodic memories that may be worth promoting to semantic.

        These are frequently-accessed memories that have proven useful.
        """
        candidates = await self.db.fetch("""
            SELECT id, user_id, topic_summary, retrieval_count, confidence
            FROM memories
            WHERE memory_type = 'episodic'
              AND retrieval_count >= $1
              AND confidence > 0.6
              AND decay_policy != 'none'
            ORDER BY retrieval_count DESC
            LIMIT 10
        """, CONSOLIDATION_THRESHOLD)

        for c in candidates:
            logger.info(
                f"Consolidation candidate: memory_id={c['id']}, "
                f"user={c['user_id']}, retrievals={c['retrieval_count']}, "
                f"confidence={c['confidence']:.2f}, "
                f"summary='{c['topic_summary'][:50]}...'"
            )

        return len(candidates)


# Convenience function for manual runs
async def run_decay_job(db_pool: asyncpg.Pool) -> dict:
    """Run decay job once (for testing or manual triggers)."""
    job = MemoryDecayJob(db_pool)
    return await job.run_decay()
```

### 4.2 Reinforcement in Retriever

```python
# src/memory/retriever.py - additions

class MemoryRetriever:
    # ... existing code ...

    async def retrieve(self, ...) -> list[RetrievedMemory]:
        """Retrieve memories with reinforcement on access."""
        # ... existing retrieval logic ...

        # Update last_accessed_at AND reinforce confidence
        if rows:
            ids = [r["id"] for r in rows]
            await self._reinforce_memories(ids)

        # ... rest of method ...

    async def _reinforce_memories(self, memory_ids: list[int]) -> None:
        """
        Reinforce accessed memories by boosting confidence and count.

        This implements the "use it or lose it" principle - memories that
        are retrieved often remain strong while unused memories decay.
        """
        await self.db.execute("""
            UPDATE memories
            SET
                confidence = LEAST(
                    CASE memory_type
                        WHEN 'semantic' THEN 0.99
                        WHEN 'procedural' THEN 0.97
                        ELSE 0.95  -- episodic
                    END,
                    confidence + CASE memory_type
                        WHEN 'semantic' THEN 0.05
                        WHEN 'procedural' THEN 0.04
                        ELSE 0.03  -- episodic
                    END
                ),
                retrieval_count = retrieval_count + 1,
                last_accessed_at = NOW()
            WHERE id = ANY($1)
        """, memory_ids)
```

### 4.3 Integration with Bot Startup

```python
# src/discord_bot.py - additions

class DiscordBot(discord.Client):
    async def setup_hook(self):
        # ... existing setup ...

        # Initialize decay job
        if self.memory_manager:
            from memory.decay import MemoryDecayJob
            self.decay_job = MemoryDecayJob(self.db_pool)
            self.decay_job.start()

    async def close(self):
        # Stop decay job
        if hasattr(self, 'decay_job'):
            self.decay_job.stop()

        # ... rest of cleanup ...
```

### 4.4 Configuration

```python
# src/memory/config.py - additions

@dataclass
class MemoryConfig:
    # ... existing fields ...

    # Decay settings
    decay_enabled: bool = True
    decay_rate: float = 0.95
    decay_period_days: int = 30
    min_confidence: float = 0.10
    reinforcement_boost_semantic: float = 0.05
    reinforcement_boost_episodic: float = 0.03
    reinforcement_boost_procedural: float = 0.04
    consolidation_threshold: int = 5

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        return cls(
            # ... existing fields ...
            decay_enabled=os.getenv("MEMORY_DECAY_ENABLED", "true").lower() == "true",
            decay_rate=float(os.getenv("MEMORY_DECAY_RATE", "0.95")),
            decay_period_days=int(os.getenv("MEMORY_DECAY_PERIOD_DAYS", "30")),
        )
```

---

## 5. CLI Tools

### 5.1 Decay Management Commands

```python
# scripts/memory_decay_cli.py

"""CLI tool for managing memory decay."""

import asyncio
import click
import asyncpg
from datetime import datetime, timezone

@click.group()
def cli():
    """Memory decay management commands."""
    pass

@cli.command()
@click.option('--dry-run', is_flag=True, help='Preview changes without applying')
async def run_decay(dry_run: bool):
    """Manually run the decay job."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])

    if dry_run:
        # Show what would be decayed (with relevance-weighted rates)
        would_decay = await pool.fetch("""
            SELECT id, user_id, topic_summary, confidence, retrieval_count, last_accessed_at,
                   confidence * POWER(
                       0.95 + (0.04 * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10)),
                       FLOOR(EXTRACT(EPOCH FROM (NOW() - last_accessed_at)) / 86400 / 30)
                   ) as new_confidence,
                   0.95 + (0.04 * LEAST(1.0, COALESCE(retrieval_count, 0)::float / 10)) as decay_rate
            FROM memories
            WHERE decay_policy = 'standard'
              AND is_protected = FALSE
              AND last_accessed_at < NOW() - INTERVAL '30 days'
              AND confidence > 0.10
            ORDER BY confidence - new_confidence DESC
            LIMIT 20
        """)

        click.echo(f"Would decay {len(would_decay)} memories:")
        for m in would_decay:
            click.echo(f"  {m['id']}: {m['confidence']:.2f} -> {m['new_confidence']:.2f} "
                      f"(rate={m['decay_rate']:.2f}, retrievals={m['retrieval_count'] or 0}) "
                      f"'{m['topic_summary'][:40]}...'")
    else:
        from memory.decay import run_decay_job
        stats = await run_decay_job(pool)
        click.echo(f"Decay complete: {stats}")

    await pool.close()

@cli.command()
@click.argument('memory_id', type=int)
async def protect(memory_id: int):
    """Protect a memory from decay."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])

    result = await pool.fetchrow("""
        UPDATE memories
        SET is_protected = TRUE, decay_policy = 'none'
        WHERE id = $1
        RETURNING id, topic_summary
    """, memory_id)

    if result:
        click.echo(f"Protected memory {memory_id}: '{result['topic_summary'][:50]}...'")
    else:
        click.echo(f"Memory {memory_id} not found")

    await pool.close()

@cli.command()
async def show_candidates():
    """Show memories that might be worth consolidating."""
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])

    candidates = await pool.fetch("""
        SELECT id, user_id, topic_summary, memory_type, retrieval_count, confidence
        FROM memories
        WHERE memory_type = 'episodic'
          AND retrieval_count >= 5
          AND confidence > 0.6
        ORDER BY retrieval_count DESC
        LIMIT 20
    """)

    click.echo(f"Found {len(candidates)} consolidation candidates:")
    for c in candidates:
        click.echo(f"  {c['id']}: retrievals={c['retrieval_count']}, "
                  f"conf={c['confidence']:.2f}, '{c['topic_summary'][:40]}...'")

    await pool.close()

if __name__ == '__main__':
    cli()
```

---

## 6. User Experience Impact

### 6.1 Claude's Language Adaptation

The existing introspection system (v0.9.20) already maps confidence to language:

| Confidence | Label | Claude's Language |
|------------|-------|-------------------|
| >= 0.9 | `stated explicitly` | "You told me..." |
| >= 0.7 | `high confidence` | "I recall..." |
| >= 0.5 | `inferred` | "I think..." |
| < 0.5 | `uncertain` | "If I remember correctly..." |

**With decay, this becomes dynamic:**

```
Day 1: "You're building an iron farm" (conf: 1.0, "stated explicitly")
Day 60: "I think you were building an iron farm?" (conf: 0.7, "high confidence")
Day 120: "If I recall, you mentioned an iron farm..." (conf: 0.5, "inferred")
```

### 6.2 Example Conversations

**Before decay (current behavior):**
```
User (4 months later): "What projects am I working on?"
Claude: "You're building an iron farm in the northeast!"
User: "No, I finished that ages ago..."
```

**After decay:**
```
User (4 months later): "What projects am I working on?"
Claude: "A few months back you mentioned an iron farm project,
but I'm not certain if that's still ongoing. What are you
working on these days?"
```

**Reinforced memory (frequently accessed):**
```
User: "What's my IGN again?"
Claude: "Your IGN is CreeperSlayer99."
(High confidence - retrieved many times, reinforced each time)
```

---

## 7. Observability

### 7.1 Analytics Events

```python
# Track decay job runs
track(
    "memory_decay_run",
    "system",
    properties={
        "decayed_count": stats["decayed_count"],
        "cleanup_flagged": stats["cleanup_flagged"],
        "consolidation_candidates": stats["consolidation_candidates"],
        "duration_ms": elapsed_ms,
    }
)

# Track reinforcement
track(
    "memory_reinforced",
    "memory",
    user_id=user_id,
    properties={
        "memory_id": memory_id,
        "old_confidence": old_conf,
        "new_confidence": new_conf,
        "retrieval_count": new_count,
    }
)
```

### 7.2 Monitoring Queries

```sql
-- Decay health check: distribution of confidence levels
SELECT
    CASE
        WHEN confidence >= 0.9 THEN 'high (0.9+)'
        WHEN confidence >= 0.7 THEN 'good (0.7-0.9)'
        WHEN confidence >= 0.5 THEN 'moderate (0.5-0.7)'
        WHEN confidence >= 0.3 THEN 'low (0.3-0.5)'
        ELSE 'very low (<0.3)'
    END as confidence_tier,
    COUNT(*) as memory_count,
    ROUND(AVG(confidence)::numeric, 2) as avg_confidence
FROM memories
GROUP BY 1
ORDER BY MIN(confidence) DESC;

-- Stale memories: not accessed in 60+ days
SELECT COUNT(*) as stale_count
FROM memories
WHERE last_accessed_at < NOW() - INTERVAL '60 days'
  AND decay_policy = 'standard';

-- Reinforcement activity: memories accessed this week
SELECT COUNT(*) as active_memories,
       AVG(retrieval_count) as avg_retrievals
FROM memories
WHERE last_accessed_at > NOW() - INTERVAL '7 days';
```

---

## 8. Testing Strategy

### 8.1 Unit Tests

```python
# tests/test_decay.py

class TestConfidenceDecay:
    async def test_decay_formula(self, db_with_memories):
        """Verify decay formula produces expected results."""
        # Insert memory with confidence 1.0, accessed 60 days ago
        # Run decay
        # Assert: confidence = 1.0 * 0.95^2 = 0.9025

    async def test_semantic_no_decay(self, db_with_memories):
        """Semantic memories should not decay."""
        # Insert semantic memory
        # Wait/simulate time passage
        # Run decay
        # Assert: confidence unchanged

    async def test_protected_no_decay(self, db_with_memories):
        """Protected memories should not decay."""
        # Insert memory with is_protected = TRUE
        # Run decay
        # Assert: confidence unchanged

    async def test_minimum_floor(self, db_with_memories):
        """Confidence should never drop below floor."""
        # Insert memory with confidence 0.15
        # Run multiple decay cycles
        # Assert: confidence >= 0.10

    async def test_reinforcement_on_access(self, retriever, db_with_memories):
        """Accessing a memory should increase confidence."""
        # Insert memory with confidence 0.7
        # Retrieve it
        # Assert: confidence increased, retrieval_count incremented


class TestConsolidation:
    async def test_consolidation_candidates(self, db_with_memories):
        """High-retrieval episodic memories should be flagged."""
        # Insert episodic memory with retrieval_count = 10
        # Run decay job
        # Assert: logged as consolidation candidate
```

### 8.2 Integration Tests

```python
class TestDecayIntegration:
    async def test_decay_job_loop(self, bot_with_decay):
        """Decay job should run on schedule."""
        # Start bot
        # Wait for first decay run
        # Verify job executed

    async def test_decay_with_retrieval(self, live_db, retriever):
        """Full cycle: decay -> retrieve -> reinforce."""
        # Insert memory
        # Run decay (confidence drops)
        # Retrieve memory (confidence rises)
        # Verify net effect
```

---

## 9. Rollout Plan

### Phase 1: Development
1. Create migration 013
2. Implement MemoryDecayJob
3. Add reinforcement to retriever
4. Add configuration options

### Phase 2: Testing
1. Unit tests
2. Integration tests
3. Manual testing with varied time scenarios
4. Verify job loop runs correctly

### Phase 3: Deployment
1. Deploy migration
2. Deploy code with decay disabled (`MEMORY_DECAY_ENABLED=false`)
3. Run manual decay to verify query performance
4. Enable decay job
5. Monitor for 48 hours

### Rollback Plan
```python
# Disable via environment
MEMORY_DECAY_ENABLED=false

# Or revert confidence values (if needed)
UPDATE memories
SET confidence = 1.0
WHERE confidence < 1.0
  AND updated_at > '2026-01-15';  -- Date of deployment
```

---

## 10. Future Enhancements

### 10.1 Automatic Consolidation

Promote frequently-accessed episodic memories to semantic:

```python
async def auto_consolidate(self, memory_id: int):
    """Promote episodic memory to semantic."""
    await self.db.execute("""
        UPDATE memories
        SET memory_type = 'semantic',
            decay_policy = 'none',
            confidence = LEAST(0.95, confidence + 0.1)
        WHERE id = $1
          AND memory_type = 'episodic'
          AND retrieval_count >= 10
    """, memory_id)
```

### 10.2 User-Controlled Protection

Allow users to protect specific memories:

```
User: "/memories protect 42"
slashAI: "Memory #42 is now protected from decay."
```

### 10.3 Decay Visualization

Show decay trajectory in memory inspector:

```
Memory #42: "User is building an iron farm"
  Created: 2025-10-15
  Last accessed: 2025-11-20 (53 days ago)
  Confidence: 0.82 (was 1.0)
  Decay trajectory: 0.78 → 0.74 → 0.70 (next 3 months)
  Status: Decaying (episodic, not reinforced)
```

---

## 11. Open Questions

1. **Should decay be user-configurable?**
   - Some users may want faster/slower decay
   - Could add `/settings decay-rate` command

2. **What's the optimal retrieval threshold for max decay resistance?**
   - Current: 10 retrievals = max resistance (0.99 decay rate)
   - May need tuning based on actual retrieval patterns
   - Could be higher (20) if most memories get retrieved often

3. **Should we notify users about decaying memories?**
   - Pro: Transparency, opportunity to reinforce
   - Con: Noise, may seem like nagging

4. **How to handle large consolidation backlogs?**
   - If many memories qualify, batch processing needed
   - Could limit to top N per user per day

5. **Should retrieval_count weight be configurable per memory type?**
   - Procedural memories might weight retrieval differently than episodic
   - Current: same formula for all non-semantic types

---

## Appendix A: Research References

- [ArXiv: Psychological Models of Memory Importance and Forgetting](https://arxiv.org/html/2409.12524v3)
- [Mem0: Production-Ready AI Agents with Long-Term Memory](https://arxiv.org/pdf/2504.19413)
- [Teaching AI to Remember: Brain-Inspired Approaches](https://www.arxiv.org/pdf/2509.00047)
- [Memory Systems in AI Agents: Episodic vs Semantic](https://ctoi.substack.com/p/memory-systems-in-ai-agents-episodic)

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial specification |
