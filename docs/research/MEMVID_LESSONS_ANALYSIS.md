# What slashAI Could Learn from Memvid: Deep Analysis

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.1.0 |
| Created | 2026-01-12 |
| Status | Research & Analysis |
| Author | Slash + Claude |
| Parent Doc | [MEMORY_COMPARISON_MEMVID.md](./MEMORY_COMPARISON_MEMVID.md) |
| References | See individual sections |

---

## Executive Summary

This document provides a deep analysis of the four enhancement opportunities identified in our Memvid comparison. For each opportunity, we cover:

1. **Current State** - How slashAI handles this today
2. **Research Findings** - What modern implementations look like
3. **Pros/Cons** - Trade-offs of implementing each enhancement
4. **Implementation Effort** - What would be involved
5. **User Experience Impact** - How end users would benefit

### Priority Summary

| Enhancement | Priority | Effort | User Impact | Recommendation |
|------------|----------|--------|-------------|----------------|
| **Hybrid Search** | High | Medium | High | Implement in v0.10.x |
| **Confidence Decay** | Medium | Low | Medium | Implement in v0.10.x |
| **Time-Travel / Audit Log** | Medium | Medium | Low-Medium | Phase into v0.11.x |
| **Deterministic Export** | Low | Low | Low | Nice-to-have |

---

## 1. Hybrid Search

### 1.1 Current State

slashAI uses **semantic-only search** via pgvector:

```python
# src/memory/retriever.py - current approach
SELECT
    id, topic_summary, raw_dialogue, ...
    1 - (embedding <=> $1::vector) as similarity
FROM memories
WHERE (privacy filters)
ORDER BY similarity DESC
LIMIT 5;
```

**What this means:**
- Queries are embedded via Voyage AI (`voyage-3.5-lite`, 1024 dimensions)
- Retrieval uses cosine distance via pgvector's `<=>` operator
- Results ranked purely by semantic similarity
- No lexical/keyword matching component

**Current limitations:**
- Exact terms like player names (IGN: "ilmango") may not match well semantically
- Coordinates ("x: 1234, z: -567") have poor embedding representation
- Mod names, specific commands, and technical jargon may miss
- A query for "CreeperSlayer99" might retrieve memories about generic creepers

### 1.2 Research Findings

Modern PostgreSQL hybrid search combines:

**Lexical Search (BM25-based):**
- `tsvector`/`tsquery` for full-text search with stemming
- True BM25 via extensions like `pg_textsearch` (TimescaleDB)
- Trigram similarity (`pg_trgm`) for fuzzy/typo matching

**Semantic Search:**
- pgvector with HNSW or IVFFlat indexes
- Cosine similarity for meaning-based retrieval

**Fusion Algorithms:**

1. **Reciprocal Rank Fusion (RRF)** - Most recommended approach
   ```
   RRF_score(doc) = 1/(k + lexical_rank) + 1/(k + semantic_rank)
   ```
   Where k is typically 50-60 (smoothing constant)

2. **Weighted Combination** - For domain-specific tuning
   ```
   final_score = (weight_lexical * lexical_score) + (weight_semantic * semantic_score)
   ```

**Why RRF is preferred:**
- Scale-independent (doesn't require normalizing incompatible score types)
- Documents appearing in multiple result sets get boosted naturally
- No hyperparameter tuning needed (k=60 works universally)

### 1.3 Pros and Cons

**Pros of Hybrid Search:**

| Benefit | Impact |
|---------|--------|
| Exact term matching | Player names, mod names, coordinates found reliably |
| Typo tolerance | "optifnie" → "OptiFine" via trigrams |
| Best-of-both | Semantic handles paraphrasing, lexical handles precision |
| Better recall | Users find what they're looking for more often |
| No additional cost | Uses existing PostgreSQL features |

**Cons of Hybrid Search:**

| Drawback | Mitigation |
|----------|------------|
| Query complexity | Encapsulate in a PostgreSQL function |
| Two index types needed | GIN for text + HNSW for vectors (already have HNSW) |
| Slight latency increase | <20ms per query (negligible vs embedding API call) |
| Schema migration | Add `tsvector` column + GIN index |

### 1.4 Implementation Approach

**Database Migration:**
```sql
-- Migration: 012_add_hybrid_search.sql

-- Add tsvector column for full-text search
ALTER TABLE memories ADD COLUMN tsv tsvector;

-- Populate from existing data
UPDATE memories SET tsv = to_tsvector('english', topic_summary || ' ' || COALESCE(raw_dialogue, ''));

-- Create GIN index for fast full-text search
CREATE INDEX idx_memories_tsv ON memories USING GIN(tsv);

-- Add trigger to maintain tsv on insert/update
CREATE FUNCTION memories_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('english', NEW.topic_summary || ' ' || COALESCE(NEW.raw_dialogue, ''));
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER memories_tsv_update
    BEFORE INSERT OR UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION memories_tsv_trigger();
```

**Hybrid Search Function:**
```sql
CREATE OR REPLACE FUNCTION hybrid_memory_search(
    query_text TEXT,
    query_embedding vector(1024),
    p_user_id BIGINT,
    p_privacy_level TEXT[],
    result_limit INT DEFAULT 5
) RETURNS TABLE (
    id INT,
    topic_summary TEXT,
    similarity FLOAT,
    rrf_score FLOAT
) AS $$
WITH lexical AS (
    SELECT m.id, m.topic_summary,
        ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, query) DESC) as lex_rank
    FROM memories m, plainto_tsquery('english', query_text) query
    WHERE m.tsv @@ query
      AND m.user_id = p_user_id
      AND m.privacy_level = ANY(p_privacy_level)
    LIMIT 20
),
semantic AS (
    SELECT m.id, m.topic_summary,
        1 - (m.embedding <=> query_embedding) as sim,
        ROW_NUMBER() OVER (ORDER BY m.embedding <=> query_embedding) as sem_rank
    FROM memories m
    WHERE m.user_id = p_user_id
      AND m.privacy_level = ANY(p_privacy_level)
    ORDER BY m.embedding <=> query_embedding
    LIMIT 20
),
fused AS (
    SELECT
        COALESCE(l.id, s.id) as id,
        COALESCE(l.topic_summary, s.topic_summary) as topic_summary,
        COALESCE(s.sim, 0) as similarity,
        (COALESCE(1.0/(60 + l.lex_rank), 0) +
         COALESCE(1.0/(60 + s.sem_rank), 0)) as rrf_score
    FROM lexical l
    FULL OUTER JOIN semantic s ON l.id = s.id
)
SELECT id, topic_summary, similarity, rrf_score
FROM fused
ORDER BY rrf_score DESC
LIMIT result_limit;
$$ LANGUAGE SQL;
```

**Python Integration:**
```python
# src/memory/retriever.py - updated
async def retrieve_hybrid(
    self,
    user_id: int,
    query: str,
    privacy_levels: list[PrivacyLevel],
    limit: int = 5
) -> list[RetrievedMemory]:
    """Retrieve memories using hybrid lexical + semantic search."""
    query_embedding = await self._embed_query(query)

    rows = await self.pool.fetch(
        "SELECT * FROM hybrid_memory_search($1, $2, $3, $4, $5)",
        query,
        query_embedding,
        user_id,
        [p.value for p in privacy_levels],
        limit
    )
    # ... process results
```

**Effort Estimate:**
- Database migration: 1 hour
- SQL function: 2-3 hours
- Python integration: 2-3 hours
- Testing: 2-3 hours
- **Total: 1-2 days**

### 1.5 User Experience Impact

**Before (semantic-only):**
```
User: "What's ilmango's farm design I mentioned?"
Claude: "I don't have any memories about that specific farm design."
         (embedding for "ilmango" doesn't match semantically)
```

**After (hybrid):**
```
User: "What's ilmango's farm design I mentioned?"
Claude: "You mentioned ilmango's witch farm design last week -
         the one using entity processing for maximum rates."
         (lexical search found exact match on "ilmango")
```

**Additional UX improvements:**
- Coordinates like "my base at x:1000" now retrievable by typing the numbers
- Command references like "/tp @p 0 64 0" findable
- Typos in player names still work via trigram fallback
- Technical terms (mod names, Minecraft jargon) match exactly

---

## 2. Confidence Decay

### 2.1 Current State

slashAI tracks confidence but has **no automatic decay**:

```python
# Current schema
confidence FLOAT DEFAULT 1.0,  # Set at extraction time
last_accessed_at TIMESTAMPTZ,  # Tracked but not used for decay
```

**What this means:**
- Confidence is set once during extraction (0.5 for inferred, 1.0 for explicit)
- Old, unreinforced memories maintain original confidence forever
- A memory from 6 months ago has same weight as one from yesterday
- `last_accessed_at` is updated on retrieval but not used

**Current limitations:**
- Episodic memories ("User was building a farm") stay permanently relevant
- Old incorrect information isn't naturally displaced
- No distinction between frequently-accessed (important) vs forgotten memories

### 2.2 Research Findings

Modern memory systems distinguish two memory types:

**Episodic Memory (should decay):**
- Specific events: "User mentioned building X on date Y"
- Temporary states: "User was debugging a crash"
- Time-bound facts: "User's current project is Z"
- Recommended decay: Based on **relevance drift** (retrieval frequency), not pure calendar time

**Semantic Memory (should not decay):**
- Persistent facts: IGN, timezone, preferences
- Learned patterns: "User prefers Fabric mods"
- Foundational context: "User plays on TBA server"
- Recommended approach: Only decay on explicit contradiction

**Best Practices:**
1. Apply different decay policies per memory type
2. Reinforce memories on access (increase confidence on retrieval)
3. **Weight decay by retrieval frequency** - frequently-retrieved memories decay slower
4. Allow consolidation: frequently-accessed episodic → semantic
5. Protect "anchor" facts marked by user confirmation
6. Use recency weighting in retrieval, not just storage

**Decay Formula (relevance-weighted):**
```python
# Relevance-weighted decay: high retrieval_count = slower decay
decay_resistance = min(1.0, retrieval_count / 10)  # 0-1 scale
effective_decay_rate = 0.95 + (0.04 * decay_resistance)  # 0.95-0.99

new_confidence = confidence * (effective_decay_rate ** periods_since_access)

# Result:
#   - 0 retrievals: decays at 0.95 (5% per period)
#   - 5 retrievals: decays at 0.97 (3% per period)
#   - 10+ retrievals: decays at 0.99 (1% per period)

# With reinforcement on access
def on_retrieval(memory):
    memory.confidence = min(0.99, memory.confidence + 0.01)
    memory.retrieval_count += 1
    memory.last_accessed_at = now()
```

**Why relevance over pure time?** A memory retrieved 50 times is demonstrably useful—it should resist decay more than one retrieved twice, even if both were last accessed the same day. Pure time-based decay treats all memories equally, missing this signal.

### 2.3 Pros and Cons

**Pros of Confidence Decay:**

| Benefit | Impact |
|---------|--------|
| Natural forgetting | Old, irrelevant memories fade |
| Self-correcting | Incorrect info eventually loses influence |
| Reinforcement learning | Frequently-used memories stay strong |
| Memory hygiene | Reduces clutter over time |
| Cognitive realism | Matches human memory patterns |

**Cons of Confidence Decay:**

| Drawback | Mitigation |
|----------|------------|
| Losing valid old memories | Protect semantic/foundational memories |
| Scheduling complexity | Use Python APScheduler (already have for reminders) |
| Threshold tuning | Start conservative (0.95 multiplier, 30-day trigger) |
| User surprise | "Why don't you remember X?" → Add transparency |

### 2.4 Implementation Approach

**Schema Update:**
```sql
-- Migration: 013_add_decay_tracking.sql

-- Add column to track decay policy
ALTER TABLE memories ADD COLUMN decay_policy TEXT DEFAULT 'standard';
-- Values: 'none' (semantic), 'standard' (episodic), 'aggressive' (temporary)

-- Add column to track retrieval count (for consolidation)
ALTER TABLE memories ADD COLUMN retrieval_count INT DEFAULT 0;

-- Add column to mark protected memories
ALTER TABLE memories ADD COLUMN is_protected BOOLEAN DEFAULT FALSE;
```

**Decay Job (Python):**
```python
# src/memory/decay.py

import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BASE_DECAY_RATE = 0.95      # Decay rate for memories with 0 retrievals
MAX_DECAY_RATE = 0.99       # Decay rate for memories with 10+ retrievals
DECAY_THRESHOLD_DAYS = 30
MIN_CONFIDENCE = 0.1
CONSOLIDATION_THRESHOLD = 5  # retrievals

async def run_decay_job(pool: asyncpg.Pool):
    """Run relevance-weighted confidence decay on episodic memories."""

    # Relevance-weighted decay: high retrieval_count = slower decay
    # effective_rate = 0.95 + (0.04 * min(1.0, retrieval_count / 10))
    await pool.execute("""
        UPDATE memories
        SET confidence = GREATEST(
            $1,
            confidence * (
                $2 + (($3 - $2) * LEAST(1.0, retrieval_count::float / 10))
            )
        )
        WHERE memory_type = 'episodic'
          AND decay_policy != 'none'
          AND is_protected = FALSE
          AND last_accessed_at < NOW() - INTERVAL '30 days'
    """, MIN_CONFIDENCE, BASE_DECAY_RATE, MAX_DECAY_RATE)

    # Flag very low confidence memories for potential cleanup
    await pool.execute("""
        UPDATE memories
        SET decay_policy = 'pending_deletion'
        WHERE confidence < $1
          AND is_protected = FALSE
          AND created_at < NOW() - INTERVAL '90 days'
    """, MIN_CONFIDENCE)

    # Log consolidation candidates (episodic → semantic)
    candidates = await pool.fetch("""
        SELECT id, topic_summary, retrieval_count, confidence
        FROM memories
        WHERE memory_type = 'episodic'
          AND retrieval_count >= $1
          AND confidence > 0.6
    """, CONSOLIDATION_THRESHOLD)

    for c in candidates:
        logger.info(f"Consolidation candidate: {c['topic_summary'][:50]}... "
                   f"(retrievals: {c['retrieval_count']})")

def schedule_decay_job(scheduler: AsyncIOScheduler, pool: asyncpg.Pool):
    """Schedule decay job to run every 6 hours."""
    scheduler.add_job(
        run_decay_job,
        'interval',
        hours=6,
        args=[pool],
        id='memory_decay',
        replace_existing=True
    )
```

**Reinforcement on Retrieval:**
```python
# src/memory/retriever.py - update existing method

async def retrieve(self, ...) -> list[RetrievedMemory]:
    memories = await self._do_retrieval(...)

    # Reinforce accessed memories (async, non-blocking)
    asyncio.create_task(self._reinforce_memories([m.id for m in memories]))

    return memories

async def _reinforce_memories(self, memory_ids: list[int]):
    """Increase confidence and retrieval count for accessed memories."""
    await self.pool.execute("""
        UPDATE memories
        SET confidence = LEAST(0.99, confidence + 0.01),
            retrieval_count = retrieval_count + 1,
            last_accessed_at = NOW()
        WHERE id = ANY($1)
    """, memory_ids)
```

**Effort Estimate:**
- Schema migration: 30 minutes
- Decay job: 2-3 hours
- Reinforcement logic: 1 hour
- Integration with existing scheduler: 1 hour
- Testing: 2 hours
- **Total: 1 day**

### 2.5 User Experience Impact

**Before (no decay):**
```
# Memory from 4 months ago still at full confidence
Memory: "User is building an iron farm" [confidence: 1.0]

User: "What am I working on?"
Claude: "You're building an iron farm!"
        (but they finished that months ago)
```

**After (with decay):**
```
# Same memory, decayed over time
Memory: "User is building an iron farm" [confidence: 0.3, old]

User: "What am I working on?"
Claude: "I'm not sure what you're currently working on.
         A while back you were building an iron farm -
         is that still going, or have you moved on to something new?"
```

**Additional UX improvements:**
- Claude's uncertainty matches actual knowledge currency
- Recent memories naturally weighted higher
- Frequently-discussed topics stay strong (reinforcement)
- Old projects don't overshadow current work
- Graceful degradation: old memories hedge rather than assert

---

## 3. Time-Travel / Audit Log

### 3.1 Current State

slashAI has **minimal audit logging**:

```sql
-- Current: Only tracks deletions
CREATE TABLE memory_deletion_log (
    id SERIAL PRIMARY KEY,
    memory_id INT,
    user_id BIGINT,
    topic_summary TEXT,
    privacy_level TEXT,
    deleted_at TIMESTAMPTZ DEFAULT NOW()
);
```

**What this means:**
- We log when memories are deleted (for recovery/compliance)
- No history of memory creation, updates, or merges
- Cannot reconstruct "what did Claude know at time X?"
- No visibility into memory evolution

**Current limitations:**
- Debug difficult: "Why did Claude say X?" - no history to check
- No rollback: Can't undo a bad merge or update
- Compliance gaps: GDPR/privacy audits lack complete trails
- User transparency: Users can't see how their data evolved

### 3.2 Research Findings

PostgreSQL offers multiple approaches:

**1. History/Shadow Tables with Triggers (Recommended)**
```sql
CREATE TABLE memories_history (
    history_id SERIAL PRIMARY KEY,
    memory_id INT,
    user_id BIGINT,
    topic_summary TEXT,
    confidence FLOAT,
    action TEXT,  -- 'INSERT', 'UPDATE', 'DELETE', 'MERGE'
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    changed_by TEXT,  -- 'extraction', 'merge', 'user', 'decay'
    previous_summary TEXT,  -- For updates
    merge_source_id INT  -- For merges
);
```

**2. "As Of" Queries**
```sql
-- What did we know about user X on date Y?
SELECT * FROM memories_history
WHERE user_id = $1
  AND changed_at <= $2
  AND (action != 'DELETE' OR changed_at > $2);
```

**3. Event Sourcing vs Audit Log**
- **Event Sourcing**: Events are the source of truth (complex, powerful)
- **Audit Log**: Shadow history for compliance (simpler, sufficient for us)

For slashAI's use case, **audit logging is sufficient** - we don't need to replay events to rebuild state.

### 3.3 Pros and Cons

**Pros of Audit Logging:**

| Benefit | Impact |
|---------|--------|
| Debug visibility | "Why did Claude say X?" becomes answerable |
| Rollback capability | Undo bad merges or accidental deletions |
| Compliance ready | GDPR audit trails, data access logs |
| User transparency | Show memory evolution on request |
| Merge tracking | See what memories combined and when |

**Cons of Audit Logging:**

| Drawback | Mitigation |
|----------|------------|
| Storage growth | Roughly doubles storage; add retention policy |
| Query complexity | Encapsulate in helper functions |
| Trigger overhead | <1ms per write operation |
| Privacy of history | Apply same privacy levels to history |

### 3.4 Implementation Approach

**Database Migration:**
```sql
-- Migration: 014_add_memory_history.sql

CREATE TABLE memories_history (
    history_id SERIAL PRIMARY KEY,

    -- Original memory fields
    memory_id INT NOT NULL,
    user_id BIGINT NOT NULL,
    topic_summary TEXT NOT NULL,
    raw_dialogue TEXT,
    memory_type TEXT,
    privacy_level TEXT,
    confidence FLOAT,

    -- Audit metadata
    action TEXT NOT NULL CHECK (action IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE')),
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    changed_by TEXT,  -- 'extraction', 'merge', 'user_delete', 'decay_job'

    -- For tracking merges
    merge_source_id INT,
    merge_reason TEXT,

    -- For tracking updates
    previous_summary TEXT,
    previous_confidence FLOAT
);

-- Index for common queries
CREATE INDEX idx_history_memory_id ON memories_history(memory_id);
CREATE INDEX idx_history_user_id ON memories_history(user_id, changed_at DESC);
CREATE INDEX idx_history_action ON memories_history(action, changed_at DESC);

-- Trigger function for automatic history tracking
CREATE OR REPLACE FUNCTION log_memory_changes() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO memories_history (
            memory_id, user_id, topic_summary, raw_dialogue,
            memory_type, privacy_level, confidence, action, changed_by
        ) VALUES (
            NEW.id, NEW.user_id, NEW.topic_summary, NEW.raw_dialogue,
            NEW.memory_type, NEW.privacy_level, NEW.confidence, 'INSERT',
            current_setting('app.changed_by', true)
        );
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO memories_history (
            memory_id, user_id, topic_summary, raw_dialogue,
            memory_type, privacy_level, confidence, action, changed_by,
            previous_summary, previous_confidence
        ) VALUES (
            NEW.id, NEW.user_id, NEW.topic_summary, NEW.raw_dialogue,
            NEW.memory_type, NEW.privacy_level, NEW.confidence, 'UPDATE',
            current_setting('app.changed_by', true),
            OLD.topic_summary, OLD.confidence
        );
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO memories_history (
            memory_id, user_id, topic_summary, raw_dialogue,
            memory_type, privacy_level, confidence, action, changed_by
        ) VALUES (
            OLD.id, OLD.user_id, OLD.topic_summary, OLD.raw_dialogue,
            OLD.memory_type, OLD.privacy_level, OLD.confidence, 'DELETE',
            current_setting('app.changed_by', true)
        );
        RETURN OLD;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER memories_audit_trigger
    AFTER INSERT OR UPDATE OR DELETE ON memories
    FOR EACH ROW EXECUTE FUNCTION log_memory_changes();

-- Retention policy: keep 180 days of history
CREATE OR REPLACE FUNCTION cleanup_memory_history() RETURNS void AS $$
BEGIN
    DELETE FROM memories_history
    WHERE changed_at < NOW() - INTERVAL '180 days';
END;
$$ LANGUAGE plpgsql;
```

**Python Integration:**
```python
# src/memory/history.py

async def set_change_context(pool: asyncpg.Pool, changed_by: str):
    """Set the context for audit logging."""
    await pool.execute("SELECT set_config('app.changed_by', $1, false)", changed_by)

async def get_memory_history(
    pool: asyncpg.Pool,
    memory_id: int
) -> list[dict]:
    """Get full history for a specific memory."""
    return await pool.fetch("""
        SELECT * FROM memories_history
        WHERE memory_id = $1
        ORDER BY changed_at DESC
    """, memory_id)

async def get_user_history_at_time(
    pool: asyncpg.Pool,
    user_id: int,
    as_of: datetime
) -> list[dict]:
    """Reconstruct what we knew about a user at a specific time."""
    return await pool.fetch("""
        WITH latest_per_memory AS (
            SELECT DISTINCT ON (memory_id) *
            FROM memories_history
            WHERE user_id = $1
              AND changed_at <= $2
            ORDER BY memory_id, changed_at DESC
        )
        SELECT * FROM latest_per_memory
        WHERE action != 'DELETE'
        ORDER BY changed_at DESC
    """, user_id, as_of)
```

**Effort Estimate:**
- Schema migration: 1-2 hours
- Trigger implementation: 1-2 hours
- Python helper functions: 2-3 hours
- CLI tool for history inspection: 2-3 hours
- Testing: 2-3 hours
- **Total: 2-3 days**

### 3.5 User Experience Impact

**For Users:**

This is primarily a behind-the-scenes improvement, but could enable:

```
User: "/memories history 42"

slashAI: Memory #42 history:
  - Created: 2025-12-15 (extraction)
    "User is building a witch farm"
  - Updated: 2025-12-20 (merge)
    "User built a witch farm based on ilmango's design"
  - Updated: 2026-01-05 (extraction)
    "User completed the witch farm with 4 platforms"
```

**For Debugging:**

```
Developer investigating "Why did Claude say the wrong thing?":

$ python scripts/memory_inspector.py history --user-id 123 --as-of "2026-01-10"
# Shows exactly what memories existed at that timestamp
# Can trace the source of bad information
```

**For Compliance:**

```
# GDPR data access request
$ python scripts/memory_inspector.py export-history --user-id 123 -o audit.json
# Complete history of all memory operations for user
```

---

## 4. Deterministic Export

### 4.1 Current State

slashAI has **JSON export** but not deterministic:

```python
# scripts/memory_inspector.py - current export
def export_memories(user_id=None, output_file=None):
    memories = fetch_memories(user_id)
    with open(output_file, 'w') as f:
        json.dump([m.to_dict() for m in memories], f, indent=2, default=str)
```

**What this means:**
- Export produces JSON that varies based on:
  - Timestamp formatting
  - Dict key ordering (Python <3.7 concern, mostly resolved)
  - Float precision variations
- Cannot reliably diff two exports to see what changed
- Not suitable for version control or reproducible tests

### 4.2 Research Findings

Memvid achieves deterministic output through:

1. **Sorted keys** - Consistent ordering in all serialization
2. **Canonical JSON** - No whitespace variations
3. **Fixed-precision floats** - Round to consistent decimal places
4. **Sorted records** - Order by ID or creation timestamp
5. **Stable timestamp format** - ISO 8601 with explicit timezone

This enables:
- `git diff` on exported memory states
- Reproducible test fixtures
- Byte-identical outputs for identical inputs
- Change detection between versions

### 4.3 Pros and Cons

**Pros of Deterministic Export:**

| Benefit | Impact |
|---------|--------|
| Version control friendly | Track memory changes in git |
| Reproducible tests | Test fixtures don't change randomly |
| Diff capability | See exactly what changed between exports |
| Integrity verification | Hash comparison for backups |

**Cons of Deterministic Export:**

| Drawback | Mitigation |
|----------|------------|
| Minimal user-facing value | Developer tooling only |
| Implementation overhead | Low (formatting changes only) |
| Larger files (no pretty-print) | Gzip for storage |

### 4.4 Implementation Approach

```python
# scripts/memory_inspector.py - updated export

import json
from datetime import datetime, timezone

def to_canonical_json(obj):
    """Convert object to canonical, deterministic JSON."""

    def serialize(o):
        if isinstance(o, datetime):
            # Always use UTC with explicit timezone
            if o.tzinfo is None:
                o = o.replace(tzinfo=timezone.utc)
            return o.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        elif isinstance(o, float):
            # Fixed precision (6 decimal places)
            return round(o, 6)
        elif hasattr(o, 'value'):  # Enum
            return o.value
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")

    return json.dumps(
        obj,
        default=serialize,
        sort_keys=True,
        separators=(',', ':'),  # No extra whitespace
        ensure_ascii=False
    )

def export_deterministic(memories: list, output_file: str):
    """Export memories in deterministic, diffable format."""

    # Sort by ID for consistent ordering
    sorted_memories = sorted(
        [m.to_dict() for m in memories],
        key=lambda m: m['id']
    )

    canonical = to_canonical_json(sorted_memories)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(canonical)

    # Also output hash for verification
    import hashlib
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    print(f"Exported {len(sorted_memories)} memories")
    print(f"Content hash: {content_hash}")
```

**Effort Estimate:**
- Update serialization: 1-2 hours
- Add hash output: 30 minutes
- Testing: 1 hour
- **Total: 3-4 hours**

### 4.5 User Experience Impact

**For Users:** None directly - this is a developer/maintenance tool.

**For Development:**

```bash
# Track memory state changes over time
$ python scripts/memory_inspector.py export --all -o memories_v1.json
# ... make changes ...
$ python scripts/memory_inspector.py export --all -o memories_v2.json

$ diff memories_v1.json memories_v2.json
# Shows exactly which memories changed and how

# Verify backup integrity
$ sha256sum memories_backup_*.json
# Identical inputs produce identical hashes
```

**For Testing:**

```python
# tests/fixtures/test_memories.json - can be committed to git
# Will not randomly change between test runs
# Test assertions can rely on exact content
```

---

## 5. Implementation Roadmap

### Phase 1: v0.10.x (High Impact, Lower Effort)

| Enhancement | Effort | Priority |
|------------|--------|----------|
| **Hybrid Search** | 1-2 days | P1 |
| **Confidence Decay** | 1 day | P1 |
| **Deterministic Export** | 3-4 hours | P3 |

**Rationale:** Hybrid search and confidence decay have the highest user-facing impact and are relatively straightforward to implement.

### Phase 2: v0.11.x (Medium Impact, Medium Effort)

| Enhancement | Effort | Priority |
|------------|--------|----------|
| **Time-Travel Audit Log** | 2-3 days | P2 |
| **History CLI Tool** | 1 day | P2 |
| **User History Command** | 1 day | P3 |

**Rationale:** Audit logging is more infrastructure-focused but valuable for debugging and compliance.

---

## 6. Summary of User Experience Changes

### Immediate Improvements (v0.10.x)

1. **Better exact-term recall**
   - "What did I say about [player name]?" now works reliably
   - Coordinates, mod names, and commands findable

2. **More appropriate confidence**
   - Old memories hedge appropriately
   - Recent memories speak confidently
   - Frequently-discussed topics stay strong

3. **Natural memory evolution**
   - Completed projects fade over time
   - Current work stays prominent
   - Corrections naturally displace old info

### Longer-Term Improvements (v0.11.x)

4. **Transparency on request**
   - Users can see how their memories evolved
   - Debugging "Why did Claude say X?" becomes possible

5. **Developer confidence**
   - Reproducible exports for testing
   - Audit trails for compliance
   - Rollback capability for mistakes

---

## Appendix A: Research Sources

### Hybrid Search
- [ParadeDB: Hybrid Search - The Missing Manual](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [Jonathan Katz: Hybrid Search with pgvector](https://jkatz05.com/post/postgres/hybrid-search-postgres-pgvector/)
- [Supabase: Hybrid Search Guide](https://supabase.com/docs/guides/ai/hybrid-search)
- [TimescaleDB: pg_textsearch](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres)
- [OpenSearch: Reciprocal Rank Fusion](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/)

### Confidence Decay
- [ArXiv: Psychological Models of Memory Importance and Forgetting](https://arxiv.org/html/2409.12524v3)
- [ArXiv: Mem0 - Production-Ready AI Agents with Long-Term Memory](https://arxiv.org/pdf/2504.19413)
- [ArXiv: Teaching AI to Remember](https://www.arxiv.org/pdf/2509.00047)
- [CTOI: Memory Systems in AI Agents](https://ctoi.substack.com/p/memory-systems-in-ai-agents-episodic)

### Time-Travel / Audit Log
- [Simple Talk: Temporal Tables in PostgreSQL](https://www.red-gate.com/simple-talk/databases/postgresql/saving-data-historically-with-temporal-tables-part-1-queries/)
- [PostgreSQL Wiki: Audit Trigger](https://wiki.postgresql.org/wiki/Audit_trigger)
- [Severalnines: PostgreSQL Audit Logging Best Practices](https://severalnines.com/blog/postgresql-audit-logging-best-practices/)
- [GitHub: postgresql-event-sourcing](https://github.com/eugene-khyst/postgresql-event-sourcing)

---

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial research and analysis |
