# Image Memory System: Issues and Remediation Plan

**Date**: 2026-01-11
**Version**: 0.9.x
**Status**: Critical issues identified requiring architectural changes

## Executive Summary

Investigation into hallucination behavior revealed four interconnected failures in the image memory system:

1. **Retrieval Gap**: Image observations are stored but never retrieved during chat
2. **Clustering Gap**: Related images don't cluster because visual similarity ≠ semantic relationship
3. **Promise Gap**: System prompt claims capabilities that aren't implemented
4. **Threshold Gap**: Image and text embeddings have completely different similarity distributions, but share thresholds

The result: Claude confidently fabricates image memories based on system prompt claims, occasionally matching real stored data by coincidence.

---

## Current State Analysis

### What Works

- **Image processing pipeline**: Images are downloaded, analyzed by Claude Vision, and embedded by Voyage AI
- **Storage**: 94 image observations stored in `image_observations` table with descriptions, tags, embeddings
- **Build clusters**: 78 clusters created in `build_clusters` table
- **Privacy classification**: Observations correctly tagged with privacy levels
- **Real-time vision**: Current-message images are processed and visible to Claude

### What Doesn't Work

| Component | Expected | Actual |
|-----------|----------|--------|
| Chat context | Image history injected into system prompt | Only text memories retrieved |
| Clustering | Same-build images grouped together | Each perspective creates new cluster |
| Cluster naming | Meaningful names like "Grand Library" | Generic "Construction Project" |
| Build narratives | Progression stories generated | `get_build_context()` never called |

---

## Issue 1: Retrieval Gap (Critical)

### Problem

The `chat()` method in `claude_client.py` only retrieves text memories:

```python
# Line 500-510 of claude_client.py
if self.memory and channel:
    memories = await self.memory.retrieve(int(user_id), content, channel)
    # ... formats text memories only
```

The `get_build_context()` method exists in `memory/manager.py:179` but is **never called**.

### Evidence

When asked "what do you remember about images I've shared":
- Retrieved: 5 text memories about *developing* image features (meta-discussion)
- Not retrieved: 94 actual image observations including "Grand library interior", "Monumental neoclassical building"
- Result: Claude hallucinated plausible Minecraft builds based on system prompt claims

### Database State

```
Tables found: ['build_clusters', 'image_observations', 'memories']
Image observations: 94
Build clusters: 78
Text memories: 176
```

### Root Cause

Implementation incomplete. The image observation pipeline was built, but integration with chat context was never finished.

### Fix Required

**Priority: P0 - Blocking**

In `claude_client.py`, modify the `chat()` method:

```python
# After retrieving text memories
memory_context = ""
build_context = ""

if self.memory and channel:
    memories = await self.memory.retrieve(int(user_id), content, channel)
    if memories:
        memory_context = self._format_memories(memories, ...)

    # NEW: Get image/build context
    build_context = await self.memory.get_build_context(int(user_id), channel)

# Combine both into system prompt
if memory_context or build_context:
    system.append({
        "type": "text",
        "text": f"{memory_context}\n\n{build_context}".strip()
    })
```

**Estimated effort**: 2-4 hours (straightforward wiring)

---

## Issue 2: Clustering Gap (Architectural)

### Problem

The clustering algorithm assumes visual similarity equals semantic relationship:

```python
# clusterer.py line 40
assignment_threshold: float = 0.72  # Min similarity to assign to existing cluster
```

### Evidence: Same Building, Different Perspectives

| Observation | Description | Cluster |
|-------------|-------------|---------|
| 105 | Neoclassical government building exterior at twilight | 70 |
| 106 | Library interior with bookshelves | 77 |
| 107 | Library interior with reading tables | 78 |

**Observations 105-107 are THE SAME BUILD** (the library), but:

| Comparison | Cosine Similarity | Threshold | Result |
|------------|-------------------|-----------|--------|
| Exterior ↔ Interior (105↔106) | 0.5015 | 0.72 | **No match** |
| Exterior ↔ Interior (105↔107) | 0.4728 | 0.72 | **No match** |
| Interior ↔ Interior (106↔107) | 0.7000 | 0.72 | **No match** |

Even two interior shots of the same room barely reach 0.70 similarity.

### Root Cause

Voyage multimodal embeddings capture **what images look like**, not **what they mean**:

- Exterior: columns, sky, snow, dusk lighting, stone facade
- Interior: bookshelves, lanterns, tables, warm lighting, wooden floors

These share almost no visual features despite being the same structure.

### Why Lowering Threshold Doesn't Help

At 0.50 threshold:
- Related images would cluster ✓
- Unrelated images would also cluster ✗ (fitness tracker + Minecraft builds)

The embedding space doesn't separate "same build different angle" from "different build similar style."

### Fixes Required

**Priority: P1 - Important**

#### Option A: User-Labeled Clusters (Recommended)

Let users explicitly name and manage their builds:

```
/build create "Grand Library"
/build add <message_id>           # Add image to current build
/build list                       # Show my builds
/build rename <id> "New Name"
```

**Pros**: Users know their builds, guaranteed accuracy
**Cons**: Requires user effort, won't help retroactively

#### Option B: Contextual Signals

Use non-visual signals to infer relationships:

1. **Temporal proximity**: Images posted within 10 minutes likely related
2. **Accompanying text**: "more of the library" → parse and link
3. **Channel context**: Same channel + similar tags → boost clustering
4. **Conversation context**: "here's the interior" after exterior → link

```python
# Enhanced clustering with context
async def assign_to_cluster(self, ..., message_context: dict):
    # Factor in:
    # - Time since last observation (< 10 min = boost)
    # - Accompanying text keywords ("more", "interior", "another angle")
    # - Previous message references

    contextual_boost = self._calculate_context_boost(message_context)
    effective_threshold = self.config.assignment_threshold - contextual_boost
```

**Pros**: Automatic, no user effort
**Cons**: Heuristics can fail, complex to tune

#### Option C: LLM-Assisted Clustering

Ask Claude to determine cluster membership:

```python
async def should_cluster(self, new_obs: dict, existing_cluster: dict) -> bool:
    prompt = f"""
    New image: {new_obs['description']}
    Tags: {new_obs['tags']}

    Existing cluster: {existing_cluster['description']}
    Recent images: {existing_cluster['recent_summaries']}

    Could this new image be part of the same Minecraft build/project?
    Consider: same structure from different angle, interior/exterior,
    progression of the same build, etc.

    Respond: YES or NO with brief reason.
    """
    # Use Haiku for cost efficiency
```

**Pros**: Semantic understanding, handles edge cases
**Cons**: API cost per image, latency, potential for LLM errors

#### Option D: Hybrid Approach (Recommended Long-term)

1. **Embedding similarity** for obvious matches (>0.80)
2. **Contextual signals** for borderline cases (0.50-0.80)
3. **User override** via slash commands
4. **LLM fallback** for ambiguous cases with user confirmation

### Recommended Implementation Order

1. **Phase 1**: User-labeled clusters (Option A) - gives users control
2. **Phase 2**: Contextual signals (Option B) - improves automatic clustering
3. **Phase 3**: LLM-assisted (Option C) - for edge cases only

---

## Issue 3: Promise Gap (Important)

### Problem

System prompt claims capabilities that don't exist:

```python
# claude_client.py lines 327-334
"""
### Image Memory
When users share images, you observe and remember them:
- Images are analyzed and stored with descriptions, tags, and embeddings
- Related images are grouped into "build clusters"
- You can track build progression over time

This means if someone shared screenshots of their base last week,
you may have context about that build.
"""
```

With retrieval broken, Claude receives this promise but no actual data. Result: confident hallucination.

### Root Cause

Documentation written for intended behavior, not actual behavior.

### Fixes Required

**Priority: P0 - Blocking (until Issue 1 fixed)**

#### Immediate Fix: Update Prompt to Match Reality

```python
"""
### Image Memory (Experimental)
When users share images, they are analyzed and stored. However, automatic
recall of past images is limited. If you're uncertain what images a user
has shared, say so rather than guessing.

For now, image memory works best for:
- Images in the current conversation
- Explicit queries like "describe the image I just shared"

Build progression tracking and automatic context injection are in development.
"""
```

#### After Issue 1 Fixed: Restore Original Claims

Once `get_build_context()` is wired up, the original system prompt claims become accurate.

#### Add Anti-Hallucination Guardrail

```python
"""
### Memory Accuracy
When asked about past interactions and no relevant memories are retrieved,
clearly state "I don't have stored memories about that" rather than
inferring or guessing. It's better to acknowledge uncertainty than to
confabulate plausible-sounding details.
"""
```

---

## Issue 4: Embedding Threshold Miscalibration (Critical)

### Problem

The system uses the same similarity threshold logic for image and text embeddings, but these have **completely different distributions**.

### Empirical Analysis

Pairwise similarity distributions across stored embeddings:

| Metric | Image (Voyage multimodal) | Text (voyage-3.5-lite) |
|--------|---------------------------|------------------------|
| **Mean** | 0.1872 | 0.6328 |
| **Std Dev** | 0.1280 | 0.0748 |
| **Min** | -0.0426 | 0.4398 |
| **Max** | 1.0000 | 0.8795 |
| **25th percentile** | 0.0974 | 0.5854 |
| **50th percentile** | 0.1739 | 0.6333 |
| **75th percentile** | 0.2642 | 0.6777 |
| **90th percentile** | 0.3556 | 0.7290 |

### Threshold Pass Rates

| Threshold | Images Pass | Text Pass |
|-----------|-------------|-----------|
| ≥ 0.30 | 18.6% | 100% |
| ≥ 0.40 | 5.7% | 100% |
| ≥ 0.50 | 1.1% | 96.3% |

### Key Insights

1. **Text embeddings are densely clustered** (mean 0.63, narrow std 0.07) - everything is somewhat similar
2. **Image embeddings are sparse** (mean 0.19, wide std 0.13) - most pairs are dissimilar
3. **The 0.30 threshold for text passes everything** - it's not filtering at all
4. **The 0.72 clustering threshold for images is nearly unreachable** - explains why even same-room shots (0.70) don't cluster

### Root Cause

Different embedding models produce different similarity distributions:
- **voyage-3.5-lite** (text): Trained on semantic similarity, produces high baseline similarity
- **Voyage multimodal**: Trained on visual similarity, produces lower baseline with more discrimination

The codebase assumed these distributions would be comparable.

### Impact

| Component | Current Threshold | Actual Meaning | Should Be |
|-----------|-------------------|----------------|-----------|
| Text retrieval | 0.30 | Passes 100% | ~0.55 (50th percentile) |
| Image clustering | 0.72 | Passes <1% | ~0.35 (90th percentile) |
| Image retrieval | (not implemented) | N/A | ~0.25 (75th percentile) |

### Recommended Thresholds

#### For Image Operations

| Use Case | Threshold | Rationale |
|----------|-----------|-----------|
| "Highly relevant" | ≥ 0.40 | Top 6% of similarities |
| "Moderately relevant" | ≥ 0.25 | Top 25% of similarities |
| Minimum retrieval | ≥ 0.15 | Top 50% of similarities |
| Cluster assignment | ≥ 0.35 | Top 10% (was 0.72!) |
| Near-duplicate detection | ≥ 0.70 | Almost identical images |

#### For Text Operations

| Use Case | Threshold | Rationale |
|----------|-----------|-----------|
| "Highly relevant" | ≥ 0.70 | Top 10% of similarities |
| "Moderately relevant" | ≥ 0.55 | Top 50% of similarities |
| Minimum retrieval | ≥ 0.45 | Bottom 5% excluded |

### Fix Required

**Priority: P0 - Blocking**

1. **Separate threshold configs** for image vs text:

```python
# memory/config.py
@dataclass
class MemoryConfig:
    # Text memory thresholds (voyage-3.5-lite)
    text_similarity_threshold: float = 0.50  # Was 0.30
    text_high_relevance: float = 0.70

    # Image memory thresholds (Voyage multimodal)
    image_similarity_threshold: float = 0.20
    image_high_relevance: float = 0.40
    image_cluster_threshold: float = 0.35  # Was 0.72!
```

2. **Update retriever.py** to use text-specific thresholds

3. **Update clusterer.py** to use image-specific thresholds:

```python
# clusterer.py - BEFORE
assignment_threshold: float = 0.72  # Unreachable for most images

# clusterer.py - AFTER
assignment_threshold: float = 0.35  # 90th percentile for images
```

4. **Add threshold to metadata labels**:

```python
def _relevance_label_for_images(self, similarity: float) -> str:
    if similarity >= 0.40:
        return "highly relevant"
    elif similarity >= 0.25:
        return "moderately relevant"
    else:
        return "tangentially relevant"
```

### Verification

After fix:
1. Run clustering on test images - verify same-room shots cluster together
2. Query "library" - verify library images retrieved with appropriate relevance labels
3. Check that unrelated images (fitness tracker) don't appear in Minecraft queries

---

## Issue 5: Cluster Naming (Minor)

### Problem

All clusters get generic names like "Construction Project" or "Project":

```
Cluster 78 (Construction Project): 1 observations
Cluster 77 (Construction Project): 1 observations
Cluster 70 (Construction Project): 3 observations
```

### Root Cause

The `_generate_cluster_name()` method uses a priority tag list that doesn't include common build types:

```python
priority_tags = [
    "castle", "house", "tower", "farm", "bridge", "cathedral",
    "village", "ship", "statue", "wall", "gate", "garden",
    "mansion", "temple", "fortress", "lighthouse",
]
```

Missing: library, government, office, shop, market, warehouse, dock, etc.

### Fix Required

**Priority: P2 - Nice to have**

1. Expand priority tag list
2. Use LLM to generate names from first observation description
3. Allow user naming via `/build rename`

---

## Issue 6: Observation Quality (Minor)

### Problem

Some observations are misclassified or have unhelpful summaries:

- Real-world photos mixed with Minecraft content
- Fitness tracker screenshots stored as observations
- Non-Minecraft content taking up storage

### Evidence

```
[obs 102] Real-world photo of two keyboard instruments - not Minecraft content
[obs 97] Fitness tracker showing 11,492 steps achieved
[obs 94] Product photo of a white 61-key MIDI keyboard controller
```

### Root Cause

The analyzer correctly identifies non-Minecraft content but stores it anyway.

### Fix Required

**Priority: P3 - Low**

Add filtering in `observer.py`:

```python
if analysis.observation_type == "not_minecraft":
    logger.info(f"Skipping non-Minecraft content: {analysis.summary[:50]}")
    return None  # Don't store
```

Or create a separate table for non-Minecraft observations if cross-content memory is desired.

---

## Implementation Roadmap

### Phase 1: Critical Fixes (Week 1)

| Task | Priority | Effort | Owner |
|------|----------|--------|-------|
| Wire up `get_build_context()` in `chat()` | P0 | 4h | - |
| Implement query-relevant image retrieval | P0 | 6h | - |
| Calibrate image vs text thresholds separately | P0 | 2h | - |
| Lower cluster assignment threshold (0.72 → 0.35) | P0 | 1h | - |
| Update system prompt to match reality | P0 | 1h | - |
| Add anti-hallucination guardrail | P0 | 1h | - |
| Test retrieval with real conversations | P0 | 2h | - |

### Phase 2: Clustering Improvements (Week 2-3)

| Task | Priority | Effort | Owner |
|------|----------|--------|-------|
| Implement `/build` slash commands | P1 | 8h | - |
| Add contextual clustering signals | P1 | 12h | - |
| Expand cluster naming vocabulary | P2 | 2h | - |
| Filter non-Minecraft observations | P3 | 2h | - |

### Phase 3: Advanced Features (Future)

| Task | Priority | Effort | Owner |
|------|----------|--------|-------|
| LLM-assisted clustering for edge cases | P2 | 16h | - |
| Build progression narratives | P2 | 8h | - |
| Cross-user build discovery | P3 | 12h | - |

---

## Verification Plan

### After Phase 1

1. Ask "what do you remember about images I've shared?"
2. Verify response includes actual stored observations
3. Verify no hallucinated details beyond stored data
4. Check logs show image_observations being queried
5. Verify image relevance labels match calibrated thresholds (≥0.40 = "highly relevant")
6. Confirm text retrieval still works with adjusted threshold (~0.50)
7. Test clustering: share two interior shots → verify they cluster together with new 0.35 threshold

### After Phase 2

1. Share exterior and interior of same build
2. Verify they cluster together (via contextual signals or user labeling)
3. Ask about "my library build" and verify both images referenced
4. Test `/build` commands for manual clustering

---

## Appendix: Database Schema Reference

### image_observations

```sql
CREATE TABLE image_observations (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,
    storage_key TEXT NOT NULL,
    storage_url TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    description TEXT,
    summary TEXT,
    tags TEXT[],
    detected_elements JSONB,
    embedding vector(1024),  -- Voyage multimodal
    observation_type TEXT,
    privacy_level TEXT,
    build_cluster_id INTEGER REFERENCES build_clusters(id),
    captured_at TIMESTAMP WITH TIME ZONE
);
```

### build_clusters

```sql
CREATE TABLE build_clusters (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    auto_name TEXT,
    user_name TEXT,  -- User-provided name (nullable)
    description TEXT,
    centroid_embedding vector(1024),
    build_type TEXT,
    style_tags TEXT[],
    observation_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    privacy_level TEXT,
    origin_guild_id BIGINT,
    first_observation_at TIMESTAMP WITH TIME ZONE,
    last_observation_at TIMESTAMP WITH TIME ZONE
);
```

---

## Appendix: Threshold Calibration Data

### Raw Analysis (2026-01-11)

```
IMAGE OBSERVATION SIMILARITIES (multimodal embeddings)
  Sample: 435 pairwise comparisons from 30 observations
  Min:   -0.0426
  Max:   1.0000
  Mean:  0.1872
  Std:   0.1280
  Percentiles: 25th=0.0974, 50th=0.1739, 75th=0.2642, 90th=0.3556

TEXT MEMORY SIMILARITIES (voyage-3.5-lite)
  Sample: 435 pairwise comparisons from 30 memories
  Min:   0.4398
  Max:   0.8795
  Mean:  0.6328
  Std:   0.0748
  Percentiles: 25th=0.5854, 50th=0.6333, 75th=0.6777, 90th=0.7290
```

### Library Image Case Study

Same building (library), different perspectives:

| Obs ID | Description | Cluster Assigned |
|--------|-------------|------------------|
| 105 | Neoclassical exterior at twilight | 70 |
| 106 | Library interior with bookshelves | 77 |
| 107 | Library interior with reading tables | 78 |

Pairwise similarities:
- 105 ↔ 106 (exterior ↔ interior): **0.5015**
- 105 ↔ 107 (exterior ↔ interior): **0.4728**
- 106 ↔ 107 (interior ↔ interior): **0.7000**

With 0.72 threshold: None clustered together (all failed)
With 0.35 threshold: 106↔107 would cluster, 105 still separate (interior/exterior gap)

### Embedding Model Reference

| Model | Used For | Dimensions | Characteristics |
|-------|----------|------------|-----------------|
| voyage-3.5-lite | Text memories | 1024 | High baseline similarity (0.44-0.88), narrow range |
| Voyage multimodal | Image observations | 1024 | Low baseline (-0.04-1.0), wide range, visual features |

---

## References

- `src/memory/images/observer.py` - Image processing pipeline
- `src/memory/images/clusterer.py` - Clustering algorithm
- `src/memory/images/narrator.py` - Build context generation
- `src/memory/manager.py` - Memory facade (contains unused `get_build_context()`)
- `src/claude_client.py` - Chat integration (missing image context retrieval)
