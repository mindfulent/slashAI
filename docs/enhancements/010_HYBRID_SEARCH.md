# Hybrid Search Implementation Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.1.0 |
| Created | 2026-01-12 |
| Status | Draft Specification |
| Author | Slash + Claude |
| Target Version | v0.10.x |
| Priority | P1 - High |

---

## 1. Problem Statement

### 1.1 Current Behavior

slashAI uses **semantic-only search** via Voyage AI embeddings and pgvector:

```python
# retriever.py:212-220
base_query = """
    SELECT ... 1 - (embedding <=> $1::vector) as similarity
    FROM memories
    WHERE 1 - (embedding <=> $1::vector) > $3
      AND ({privacy_filter})
    ORDER BY embedding <=> $1::vector
    LIMIT $4
"""
```

This works well for conceptual queries but fails for:

| Query Type | Example | Problem |
|------------|---------|---------|
| Player names | "What did ilmango say?" | "ilmango" has no semantic meaning |
| Coordinates | "My base at x:1000" | Numbers embed poorly |
| Mod names | "Install OptiFine" | Technical terms vary in embedding |
| Commands | "/tp @p 0 64 0" | Syntax has no semantic representation |
| Exact phrases | "creeper farm" vs "mob farm" | Should find exact match first |

### 1.2 User Impact

**Scenario 1: Player Name Search**
```
User: "What do you remember about CreeperSlayer99?"

Current (semantic-only):
  → Retrieves memories about creepers, slaying, gaming
  → Misses the exact player name match
  → Claude: "I don't have specific memories about CreeperSlayer99"

Desired (hybrid):
  → Lexical search finds exact "CreeperSlayer99" match
  → Claude: "CreeperSlayer99 built a witch farm near spawn last week"
```

**Scenario 2: Coordinate Search**
```
User: "What's at coordinates 500, 64, -200?"

Current:
  → Numbers embed to generic "coordinate" concept
  → Returns any memory mentioning coordinates
  → Low precision

Desired:
  → Lexical search finds exact number matches
  → High precision on specific locations
```

### 1.3 Success Criteria

1. Exact term queries return exact matches with high precision
2. Semantic queries continue to work for conceptual searches
3. Combined queries benefit from both approaches
4. Latency increase < 50ms per query
5. No degradation of privacy filtering

---

## 2. Technical Design

### 2.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Hybrid Search Architecture                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  User Query ──────────────────────────────────────────────────────────────┐ │
│       │                                                                   │ │
│       ├───────────────────┬───────────────────────────────────────────────┤ │
│       │                   │                                               │ │
│       ▼                   ▼                                               │ │
│  ┌─────────────┐    ┌─────────────┐                                       │ │
│  │  Lexical    │    │  Semantic   │                                       │ │
│  │  Search     │    │  Search     │                                       │ │
│  │             │    │             │                                       │ │
│  │  tsvector   │    │  pgvector   │                                       │ │
│  │  GIN index  │    │  HNSW index │                                       │ │
│  │  ts_rank_cd │    │  cosine     │                                       │ │
│  └──────┬──────┘    └──────┬──────┘                                       │ │
│         │                  │                                               │ │
│         │   Top 20 each    │                                               │ │
│         └────────┬─────────┘                                               │ │
│                  │                                                         │ │
│                  ▼                                                         │ │
│         ┌───────────────┐                                                  │ │
│         │ Reciprocal    │                                                  │ │
│         │ Rank Fusion   │                                                  │ │
│         │               │                                                  │ │
│         │ RRF(d) = Σ    │                                                  │ │
│         │ 1/(k+rank_i)  │                                                  │ │
│         └───────┬───────┘                                                  │ │
│                 │                                                          │ │
│                 ▼                                                          │ │
│         ┌───────────────┐                                                  │ │
│         │ Privacy       │                                                  │ │
│         │ Filter        │                                                  │ │
│         │ (applied in   │                                                  │ │
│         │  both CTEs)   │                                                  │ │
│         └───────┬───────┘                                                  │ │
│                 │                                                          │ │
│                 ▼                                                          │ │
│         Top K Results (default 5)                                          │ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Reciprocal Rank Fusion (RRF)

**Why RRF over weighted scoring:**

1. **Scale-independent** - BM25 scores (0-∞) and cosine similarity (0-1) are incomparable
2. **No normalization needed** - RRF uses rank positions, not raw scores
3. **Natural boosting** - Documents in both result sets get higher combined scores
4. **No hyperparameter tuning** - k=60 works universally across domains

**Formula:**
```
RRF_score(document) = Σ 1/(k + rank_i(document))

Where:
  - k = 60 (smoothing constant, prevents extreme scores)
  - rank_i = rank in result set i (lexical or semantic)
  - Σ = sum over all result sets where document appears
```

**Example:**
```
Document X:
  - Lexical rank: 3   → 1/(60+3) = 0.0159
  - Semantic rank: 7  → 1/(60+7) = 0.0149
  - RRF score: 0.0308

Document Y:
  - Lexical rank: N/A (not in lexical results)
  - Semantic rank: 1  → 1/(60+1) = 0.0164
  - RRF score: 0.0164

Document X wins despite lower semantic rank because it appears in both result sets.
```

### 2.3 Full-Text Search Configuration

**Language Configuration:**
```sql
-- Use English stemmer for Minecraft/gaming context
-- "building" → "build", "creepers" → "creeper"
SELECT to_tsvector('english', 'User is building farms to kill creepers');
-- Result: 'build':3 'creeper':7 'farm':4 'kill':6 'user':1
```

**Custom Dictionary Considerations:**
- Player names (IGN) should NOT be stemmed
- Mod names should NOT be stemmed
- Minecraft terms should use standard English rules

**Solution:** Use `simple` config for exact matching alongside `english`:
```sql
-- Combined tsvector with both configurations
tsv = setweight(to_tsvector('simple', topic_summary), 'A') ||
      setweight(to_tsvector('english', topic_summary || ' ' || raw_dialogue), 'B')
```

This gives higher weight ('A') to exact matches and lower weight ('B') to stemmed matches.

---

## 3. Database Schema Changes

### 3.1 Migration: Add tsvector Column

```sql
-- migrations/012_add_hybrid_search.sql

-- Part 1: Add tsvector column
ALTER TABLE memories ADD COLUMN IF NOT EXISTS tsv tsvector;

-- Part 2: Populate existing data with weighted vectors
-- Weight A = exact matches (player names, mod names)
-- Weight B = stemmed matches (descriptions, dialogue)
UPDATE memories SET tsv =
    setweight(to_tsvector('simple', topic_summary), 'A') ||
    setweight(to_tsvector('english', COALESCE(topic_summary, '') || ' ' || COALESCE(raw_dialogue, '')), 'B');

-- Part 3: Create GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS idx_memories_tsv ON memories USING GIN(tsv);

-- Part 4: Create trigger to maintain tsvector on insert/update
CREATE OR REPLACE FUNCTION memories_tsv_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsv :=
        setweight(to_tsvector('simple', NEW.topic_summary), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.topic_summary, '') || ' ' || COALESCE(NEW.raw_dialogue, '')), 'B');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS memories_tsv_update ON memories;
CREATE TRIGGER memories_tsv_update
    BEFORE INSERT OR UPDATE OF topic_summary, raw_dialogue ON memories
    FOR EACH ROW EXECUTE FUNCTION memories_tsv_trigger();

-- Part 5: Add index hints for performance
COMMENT ON INDEX idx_memories_tsv IS 'GIN index for hybrid lexical search - created by migration 012';
```

### 3.2 Storage Impact

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Per-memory size | ~2-4KB | ~2.5-5KB | +25% |
| Index size (1K memories) | ~4MB | ~5MB | +25% |
| Index size (10K memories) | ~40MB | ~50MB | +25% |

**Note:** The tsvector column adds minimal storage because it only stores unique lexemes with positions, not full text.

---

## 4. SQL Implementation

### 4.1 Hybrid Search Function

```sql
-- Stored function for hybrid memory search with privacy filtering
CREATE OR REPLACE FUNCTION hybrid_memory_search(
    query_text TEXT,
    query_embedding vector(1024),
    p_user_id BIGINT,
    p_context_privacy TEXT,
    p_guild_id BIGINT DEFAULT NULL,
    p_channel_id BIGINT DEFAULT NULL,
    result_limit INT DEFAULT 5,
    candidate_limit INT DEFAULT 20
) RETURNS TABLE (
    id INT,
    user_id BIGINT,
    topic_summary TEXT,
    raw_dialogue TEXT,
    memory_type TEXT,
    privacy_level TEXT,
    confidence FLOAT,
    updated_at TIMESTAMPTZ,
    similarity FLOAT,
    rrf_score FLOAT
) AS $$
DECLARE
    k CONSTANT INT := 60;  -- RRF smoothing constant
BEGIN
    RETURN QUERY
    WITH
    -- Build privacy filter based on context
    privacy_filter AS (
        SELECT m.id
        FROM memories m
        WHERE
            CASE p_context_privacy
                WHEN 'dm' THEN
                    m.user_id = p_user_id
                WHEN 'channel_restricted' THEN
                    (m.user_id = p_user_id AND m.privacy_level = 'global')
                    OR (m.privacy_level = 'guild_public' AND m.origin_guild_id = p_guild_id)
                    OR (m.user_id = p_user_id AND m.privacy_level = 'channel_restricted'
                        AND m.origin_channel_id = p_channel_id)
                WHEN 'guild_public' THEN
                    (m.user_id = p_user_id AND m.privacy_level = 'global')
                    OR (m.privacy_level = 'guild_public' AND m.origin_guild_id = p_guild_id)
                ELSE FALSE
            END
    ),

    -- Lexical search using ts_rank_cd (cover density)
    lexical AS (
        SELECT
            m.id,
            ts_rank_cd(m.tsv, query) AS lex_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(m.tsv, query) DESC) AS lex_rank
        FROM memories m, plainto_tsquery('english', query_text) query
        WHERE m.tsv @@ query
          AND m.id IN (SELECT pf.id FROM privacy_filter pf)
        ORDER BY ts_rank_cd(m.tsv, query) DESC
        LIMIT candidate_limit
    ),

    -- Semantic search using pgvector cosine distance
    semantic AS (
        SELECT
            m.id,
            1 - (m.embedding <=> query_embedding) AS sem_score,
            ROW_NUMBER() OVER (ORDER BY m.embedding <=> query_embedding) AS sem_rank
        FROM memories m
        WHERE m.id IN (SELECT pf.id FROM privacy_filter pf)
        ORDER BY m.embedding <=> query_embedding
        LIMIT candidate_limit
    ),

    -- Reciprocal Rank Fusion
    fused AS (
        SELECT
            COALESCE(l.id, s.id) AS id,
            COALESCE(s.sem_score, 0) AS similarity,
            (COALESCE(1.0 / (k + l.lex_rank), 0) +
             COALESCE(1.0 / (k + s.sem_rank), 0)) AS rrf_score
        FROM lexical l
        FULL OUTER JOIN semantic s ON l.id = s.id
    )

    -- Final result with all memory fields
    SELECT
        m.id,
        m.user_id,
        m.topic_summary,
        m.raw_dialogue,
        m.memory_type,
        m.privacy_level,
        COALESCE(m.confidence, 0.5) AS confidence,
        m.updated_at,
        f.similarity,
        f.rrf_score
    FROM fused f
    JOIN memories m ON f.id = m.id
    ORDER BY f.rrf_score DESC
    LIMIT result_limit;
END;
$$ LANGUAGE plpgsql STABLE;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION hybrid_memory_search TO slashai_app;
```

### 4.2 Query Examples

**Example 1: Player Name Query**
```sql
SELECT * FROM hybrid_memory_search(
    'ilmango',                          -- query_text
    '[0.1, 0.2, ...]'::vector,          -- query_embedding (from Voyage)
    123456789,                           -- user_id
    'guild_public',                      -- context_privacy
    987654321,                           -- guild_id
    NULL,                                -- channel_id (not needed for guild_public)
    5,                                   -- result_limit
    20                                   -- candidate_limit
);
```

**Example 2: Coordinate Query**
```sql
SELECT * FROM hybrid_memory_search(
    'x:1000 z:-500',
    '[...]'::vector,
    123456789,
    'dm',
    NULL,
    NULL,
    5,
    20
);
```

---

## 5. Python Integration

### 5.1 Updated Retriever

```python
# src/memory/retriever.py

class MemoryRetriever:
    """Retrieves relevant memories with hybrid lexical + semantic search."""

    async def retrieve(
        self,
        user_id: int,
        query: str,
        channel: discord.abc.Messageable,
        top_k: Optional[int] = None,
    ) -> list[RetrievedMemory]:
        """
        Retrieve relevant memories using hybrid search with privacy filtering.

        Combines lexical (BM25-style) and semantic (embedding) search using
        Reciprocal Rank Fusion for optimal recall across query types.
        """
        if not query or not query.strip():
            return []

        top_k = top_k or self.config.top_k
        context_privacy = await classify_channel_privacy(channel)

        # Get channel/guild IDs for privacy filtering
        guild_id = getattr(channel, 'guild', None)
        guild_id = guild_id.id if guild_id else None
        channel_id = getattr(channel, 'id', None)

        # Generate query embedding
        embedding = await self._embed(query, input_type="query")
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        # Execute hybrid search
        rows = await self.db.fetch(
            """SELECT * FROM hybrid_memory_search($1, $2, $3, $4, $5, $6, $7, $8)""",
            query,                          # query_text
            embedding_str,                  # query_embedding
            user_id,                        # p_user_id
            context_privacy.value,          # p_context_privacy
            guild_id,                       # p_guild_id
            channel_id,                     # p_channel_id
            top_k,                          # result_limit
            20,                             # candidate_limit (retrieve more for RRF)
        )

        # Update last_accessed_at for retrieved memories
        if rows:
            ids = [r["id"] for r in rows]
            await self.db.execute(
                "UPDATE memories SET last_accessed_at = NOW() WHERE id = ANY($1)", ids
            )

        memories = [
            RetrievedMemory(
                id=r["id"],
                user_id=r["user_id"],
                summary=r["topic_summary"],
                raw_dialogue=r["raw_dialogue"],
                memory_type=r["memory_type"],
                privacy_level=PrivacyLevel(r["privacy_level"]),
                similarity=r["similarity"],
                confidence=r["confidence"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

        # Debug logging
        if memories:
            logger.debug(
                f"Hybrid search for '{query[:50]}...' returned {len(memories)} memories:\n" +
                "\n".join(
                    f"  - {m.id}: RRF={rows[i]['rrf_score']:.4f}, sim={m.similarity:.3f}, "
                    f"'{m.summary[:40]}...'"
                    for i, m in enumerate(memories)
                )
            )

        return memories
```

### 5.2 Configuration Updates

```python
# src/memory/config.py

@dataclass
class MemoryConfig:
    # ... existing fields ...

    # Hybrid search settings
    hybrid_search_enabled: bool = True
    hybrid_candidate_limit: int = 20  # Candidates per search type for RRF
    rrf_k: int = 60  # Smoothing constant for RRF

    # Fallback to semantic-only if lexical returns no results
    hybrid_fallback_semantic: bool = True

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        return cls(
            # ... existing fields ...
            hybrid_search_enabled=os.getenv("MEMORY_HYBRID_SEARCH", "true").lower() == "true",
            hybrid_candidate_limit=int(os.getenv("MEMORY_HYBRID_CANDIDATES", "20")),
        )
```

---

## 6. Performance Analysis

### 6.1 Query Performance

| Operation | Current (semantic-only) | Hybrid | Delta |
|-----------|------------------------|--------|-------|
| Embedding generation | ~80ms | ~80ms | 0 |
| Vector search (HNSW) | ~15ms | ~15ms | 0 |
| Lexical search (GIN) | N/A | ~10ms | +10ms |
| RRF computation | N/A | <1ms | +1ms |
| **Total** | ~100ms | ~110ms | +10% |

### 6.2 Index Performance

| Index Type | Build Time (1K docs) | Build Time (10K docs) | Memory |
|------------|---------------------|----------------------|--------|
| HNSW (existing) | ~2s | ~20s | ~40MB/10K |
| GIN (new) | <1s | ~5s | ~10MB/10K |

### 6.3 Benchmark Plan

```python
# tests/benchmark_hybrid_search.py

import asyncio
import time

BENCHMARK_QUERIES = [
    # Exact term queries (should benefit most from hybrid)
    ("ilmango", "player_name"),
    ("CreeperSlayer99", "player_name"),
    ("x:1000 z:-500", "coordinates"),
    ("OptiFine", "mod_name"),

    # Conceptual queries (semantic should still work)
    ("how to build a mob farm", "conceptual"),
    ("efficient sorting system", "conceptual"),
    ("survival base ideas", "conceptual"),

    # Mixed queries (both should help)
    ("ilmango's iron farm design", "mixed"),
    ("build at coordinates 500, 64, 200", "mixed"),
]

async def benchmark_search(retriever, query, expected_type):
    start = time.perf_counter()
    results = await retriever.retrieve(user_id=123, query=query, channel=mock_channel)
    elapsed = (time.perf_counter() - start) * 1000

    return {
        "query": query,
        "type": expected_type,
        "latency_ms": elapsed,
        "result_count": len(results),
        "top_similarity": results[0].similarity if results else 0,
    }
```

---

## 7. Testing Strategy

### 7.1 Unit Tests

```python
# tests/test_hybrid_search.py

class TestHybridSearch:
    """Test hybrid search functionality."""

    async def test_exact_term_match(self, retriever, db_with_memories):
        """Exact player name should be found with high confidence."""
        # Setup: Memory with "IGN: ilmango"
        # Query: "ilmango"
        # Assert: Memory found with high RRF score

    async def test_coordinate_search(self, retriever, db_with_memories):
        """Coordinate queries should match exact numbers."""
        # Setup: Memory with "Base at x:1000, z:-500"
        # Query: "1000 -500"
        # Assert: Memory found

    async def test_semantic_still_works(self, retriever, db_with_memories):
        """Conceptual queries should still use semantic search."""
        # Setup: Memory about "efficient resource gathering"
        # Query: "how to collect materials quickly"
        # Assert: Memory found via semantic similarity

    async def test_privacy_preserved(self, retriever, db_with_memories):
        """Privacy filters should apply to hybrid search."""
        # Setup: DM memory for user A
        # Query from: User B in public channel
        # Assert: Memory NOT found

    async def test_rrf_fusion(self, retriever, db_with_memories):
        """Documents in both result sets should rank higher."""
        # Setup: Memory matching both lexical and semantic
        # Assert: Higher RRF score than single-match memories
```

### 7.2 Integration Tests

```python
class TestHybridSearchIntegration:
    """End-to-end hybrid search tests."""

    async def test_real_player_names(self, live_db):
        """Test with real Minecraft player name patterns."""
        test_igns = ["Dream", "Technoblade", "Ph1LzA", "Grian", "MumboJumbo"]
        # Insert memories, query each, verify recall

    async def test_performance_regression(self, live_db, benchmark_baseline):
        """Ensure hybrid doesn't regress latency significantly."""
        # Run benchmark suite
        # Assert: p95 latency < 150ms
        # Assert: Improvement on exact-term queries
```

---

## 8. Rollout Plan

### Phase 1: Development
1. Create migration 012_add_hybrid_search.sql
2. Implement hybrid_memory_search SQL function
3. Update MemoryRetriever to use hybrid search
4. Add configuration flag for hybrid search

### Phase 2: Testing
1. Run unit tests
2. Run integration tests
3. Benchmark latency vs baseline
4. Test privacy filtering edge cases

### Phase 3: Deployment
1. Deploy migration to staging
2. Enable hybrid search on staging
3. Monitor for 24 hours
4. Deploy to production with feature flag
5. Gradually enable for users

### Rollback Plan
```python
# If issues arise, disable via environment variable
MEMORY_HYBRID_SEARCH=false

# Or revert to previous retriever behavior
async def retrieve(self, ...):
    if not self.config.hybrid_search_enabled:
        return await self._retrieve_semantic_only(...)
    return await self._retrieve_hybrid(...)
```

---

## 9. Future Enhancements

### 9.1 Trigram Fuzzy Matching

Add `pg_trgm` for typo tolerance:

```sql
-- Future: Add trigram index for fuzzy matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_memories_trgm ON memories USING GIN(topic_summary gin_trgm_ops);

-- Query with fuzzy matching
SELECT * FROM memories
WHERE topic_summary % 'optifnie'  -- Matches "OptiFine" despite typo
```

### 9.2 Weighted RRF

Allow domain-specific tuning:

```python
# For fact-checking queries, weight lexical higher
lexical_weight = 0.7 if query_type == "fact" else 0.5
semantic_weight = 1.0 - lexical_weight

rrf_score = (lexical_weight * 1/(k + lex_rank)) + (semantic_weight * 1/(k + sem_rank))
```

### 9.3 Query Classification

Automatically detect query type for optimal search strategy:

```python
def classify_query(query: str) -> QueryType:
    # Detect coordinates: x:123 or numbers
    if re.search(r'[xyz]:\s*-?\d+', query):
        return QueryType.COORDINATES

    # Detect player names: CamelCase, numbers in name
    if re.match(r'^[A-Za-z0-9_]{3,16}$', query):
        return QueryType.PLAYER_NAME

    # Detect mod names: common patterns
    if query.lower() in MOD_NAME_LIST:
        return QueryType.MOD_NAME

    return QueryType.CONCEPTUAL
```

---

## 10. Open Questions

1. **Should we use `websearch_to_tsquery` for complex queries?**
   - Currently using `plainto_tsquery` (simple word matching)
   - `websearch_to_tsquery` supports quoted phrases and operators
   - Trade-off: More powerful but harder to predict

2. **What's the optimal candidate_limit for RRF?**
   - Currently: 20 per search type
   - Lower = faster but may miss good matches
   - Higher = more comprehensive but slower
   - Need benchmarking to optimize

3. **Should lexical search include raw_dialogue or just topic_summary?**
   - Currently: Both (weighted)
   - Pro: Better recall for specific phrases
   - Con: More noise from dialogue snippets

---

## Appendix A: References

- [ParadeDB: Hybrid Search in PostgreSQL](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [Supabase: Hybrid Search Guide](https://supabase.com/docs/guides/ai/hybrid-search)
- [OpenSearch: Reciprocal Rank Fusion](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/)
- [PostgreSQL Full-Text Search Docs](https://www.postgresql.org/docs/current/datatype-textsearch.html)
- [pgvector GitHub](https://github.com/pgvector/pgvector)

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial specification |
