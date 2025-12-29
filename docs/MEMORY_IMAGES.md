# Image Memory System Technical Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.9.8 |
| Last Updated | 2025-12-28 |
| Status | Released |
| Author | Slash + Claude |
| Parent Docs | [MEMORY_TECHSPEC.md](./MEMORY_TECHSPEC.md), [MEMORY_PRIVACY.md](./MEMORY_PRIVACY.md) |

---

## 1. Overview

### 1.1 Problem Statement

Point-in-time image interpretation is table stakes. When a user shares a screenshot of their Minecraft build, slashAI can describe what it sees—but it immediately forgets. The next image is interpreted in isolation, with no understanding that it shows the *same build* two weeks later with a new tower added.

This limitation prevents slashAI from:
- Tracking build progression over time
- Recognizing returning projects ("your castle is coming along!")
- Generating narrative stories about a user's creative journey
- Providing contextual feedback that references past work

### 1.2 Goals

The v0.9.2 image memory system will:

1. **Persist image observations** with descriptions, embeddings, and raw storage
2. **Cluster observations into builds/projects** using semantic similarity and temporal proximity
3. **Track progression** with timestamped milestones and detected changes
4. **Generate narratives** that tell the story of a user's builds over time
5. **Enforce content safety** with active moderation for inappropriate images
6. **Inherit privacy boundaries** from the existing channel-based privacy model

### 1.3 Non-Goals (v0.9.2)

- User-facing commands for project management (`/builds`, `/projects`)
- Automatic milestone detection with notifications
- Cross-user build comparison or community galleries
- Video/GIF analysis (static images only)
- Non-Minecraft image categorization (memes, IRL photos)

### 1.4 Relationship to Text Memory

Image observations are a **source of memories**, not memories themselves.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Memory System Architecture                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌──────────────────┐     ┌──────────────────┐                         │
│   │  Conversations   │     │  Image Shares    │                         │
│   │  (text messages) │     │  (attachments)   │                         │
│   └────────┬─────────┘     └────────┬─────────┘                         │
│            │                        │                                    │
│            ▼                        ▼                                    │
│   ┌──────────────────┐     ┌──────────────────┐                         │
│   │    Sessions      │     │ Image Observations│                        │
│   │ (message buffer) │     │ (per-image record)│                        │
│   └────────┬─────────┘     └────────┬─────────┘                         │
│            │                        │                                    │
│            │                        ▼                                    │
│            │               ┌──────────────────┐                         │
│            │               │  Build Clusters  │                         │
│            │               │ (grouped images) │                         │
│            │               └────────┬─────────┘                         │
│            │                        │                                    │
│            ▼                        ▼                                    │
│   ┌─────────────────────────────────────────────┐                       │
│   │              Memory Extraction              │                       │
│   │  (combines text + image context → memories) │                       │
│   └─────────────────────────────────────────────┘                       │
│                            │                                             │
│                            ▼                                             │
│   ┌─────────────────────────────────────────────┐                       │
│   │              memories table                 │                       │
│   │     (unified semantic + episodic store)     │                       │
│   └─────────────────────────────────────────────┘                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

A memory might be: *"User is building a gothic castle with intricate tower details"*—derived from multiple image observations over time.

---

## 2. Database Schema

### 2.1 Image Observations Table

```sql
-- Individual image observations (one per shared image)
CREATE TABLE image_observations (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    
    -- Discord context
    message_id BIGINT NOT NULL UNIQUE,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,                              -- NULL for DMs
    
    -- Storage references
    storage_key TEXT NOT NULL,                    -- DO Spaces key: "images/{user_id}/{hash}.png"
    storage_url TEXT NOT NULL,                    -- Full DO Spaces URL
    original_url TEXT,                            -- Discord CDN URL (may expire)
    file_hash TEXT NOT NULL,                      -- SHA-256 for deduplication
    file_size_bytes INT,
    dimensions TEXT,                              -- "1920x1080"
    
    -- Visual analysis (from Claude)
    description TEXT NOT NULL,                    -- Detailed interpretation
    summary TEXT NOT NULL,                        -- One-line summary for retrieval
    tags TEXT[] DEFAULT '{}',                     -- ["castle", "medieval", "stone", "tower"]
    detected_elements JSONB DEFAULT '{}',         -- Structured: {"biome": "plains", "structures": ["tower", "wall"]}
    
    -- Embedding (Voyage multimodal-3)
    embedding vector(1024) NOT NULL,
    
    -- Classification
    observation_type TEXT DEFAULT 'unknown',      -- build_progress, landscape, redstone, farm, other
    build_cluster_id INT REFERENCES build_clusters(id) ON DELETE SET NULL,
    
    -- Privacy (inherited from channel at capture time)
    privacy_level TEXT NOT NULL,
    
    -- Context
    accompanying_text TEXT,                       -- User's message with the image, if any
    conversation_context TEXT,                    -- Recent messages before/after for context
    
    -- Timestamps
    captured_at TIMESTAMPTZ NOT NULL,             -- When user shared in Discord
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT observation_type_valid 
        CHECK (observation_type IN ('build_progress', 'landscape', 'redstone', 'farm', 'other', 'unknown')),
    CONSTRAINT privacy_level_valid 
        CHECK (privacy_level IN ('dm', 'channel_restricted', 'guild_public', 'global'))
);

-- Indexes
CREATE INDEX obs_user_id_idx ON image_observations(user_id);
CREATE INDEX obs_cluster_idx ON image_observations(build_cluster_id);
CREATE INDEX obs_captured_idx ON image_observations(captured_at DESC);
CREATE INDEX obs_privacy_idx ON image_observations(user_id, privacy_level, guild_id, channel_id);

-- Vector similarity search
CREATE INDEX obs_embedding_idx ON image_observations 
    USING ivfflat (embedding vector_cosine_ops) 
    WITH (lists = 100);
```

### 2.2 Build Clusters Table

```sql
-- Inferred build/project clusters (groups of related observations)
CREATE TABLE build_clusters (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    
    -- Cluster identity
    auto_name TEXT NOT NULL,                      -- Generated: "Medieval Castle #1"
    user_name TEXT,                               -- User-provided override (future)
    description TEXT,                             -- Summary of the build's evolution
    
    -- Embedding (centroid of member observations)
    centroid_embedding vector(1024),
    
    -- Classification
    build_type TEXT DEFAULT 'unknown',            -- castle, house, farm, redstone, landscape, mixed
    style_tags TEXT[] DEFAULT '{}',               -- ["medieval", "gothic", "stone"]
    
    -- Progression tracking
    status TEXT DEFAULT 'active',                 -- active, completed, abandoned
    observation_count INT DEFAULT 0,
    
    -- Milestone tracking (JSONB array)
    milestones JSONB DEFAULT '[]',                -- [{"date": "...", "description": "Foundation complete", "observation_id": 5}]
    
    -- Privacy (most restrictive of all member observations)
    privacy_level TEXT NOT NULL,
    origin_guild_id BIGINT,                       -- Primary guild for this build
    
    -- Timestamps
    first_observation_at TIMESTAMPTZ,
    last_observation_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT cluster_status_valid 
        CHECK (status IN ('active', 'completed', 'abandoned'))
);

-- Indexes
CREATE INDEX cluster_user_idx ON build_clusters(user_id);
CREATE INDEX cluster_status_idx ON build_clusters(user_id, status);
CREATE INDEX cluster_privacy_idx ON build_clusters(user_id, privacy_level, origin_guild_id);
CREATE INDEX cluster_updated_idx ON build_clusters(updated_at DESC);

-- Vector similarity for finding related builds
CREATE INDEX cluster_centroid_idx ON build_clusters 
    USING ivfflat (centroid_embedding vector_cosine_ops) 
    WITH (lists = 50);
```

### 2.3 Moderation Log Table

```sql
-- Log of moderated images (text description only, no image storage)
CREATE TABLE image_moderation_log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    
    -- Discord context
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,
    
    -- Moderation details
    violation_type TEXT NOT NULL,                 -- 'nsfw', 'violence', 'illegal', 'other'
    violation_description TEXT NOT NULL,          -- Text description for admin review
    confidence FLOAT NOT NULL,                    -- Model confidence 0.0-1.0
    
    -- Actions taken
    message_deleted BOOLEAN DEFAULT FALSE,
    user_warned BOOLEAN DEFAULT FALSE,
    admin_notified BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX mod_log_user_idx ON image_moderation_log(user_id);
CREATE INDEX mod_log_guild_idx ON image_moderation_log(guild_id);
```

---

## 3. Architecture

### 3.1 Proposed File Structure

```
slashAI/
├── src/
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── config.py              # Extended with image settings
│   │   ├── privacy.py             # Unchanged
│   │   ├── extractor.py           # Extended to incorporate image context
│   │   ├── retriever.py           # Extended for image-aware retrieval
│   │   ├── updater.py             # Unchanged
│   │   ├── manager.py             # Extended with image methods
│   │   │
│   │   └── images/                # NEW: Image subsystem
│   │       ├── __init__.py
│   │       ├── observer.py        # ImageObserver - processes incoming images
│   │       ├── analyzer.py        # ImageAnalyzer - Claude vision + moderation
│   │       ├── clusterer.py       # BuildClusterer - groups observations
│   │       ├── narrator.py        # BuildNarrator - generates progression stories
│   │       └── storage.py         # ImageStorage - DO Spaces interface
│   │
│   └── ...
│
├── migrations/
│   ├── 001_create_memories.sql
│   ├── 002_create_sessions.sql
│   ├── 003_add_indexes.sql
│   ├── 004_create_image_observations.sql   # NEW
│   ├── 005_create_build_clusters.sql       # NEW
│   └── 006_create_moderation_log.sql       # NEW
```

### 3.2 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Image Memory Pipeline                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Discord Image Received                                                          │
│         │                                                                        │
│         ▼                                                                        │
│  ┌─────────────────┐                                                            │
│  │ ImageObserver   │ ─── Entry point, extracts image from Discord message       │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐     ┌─────────────────┐                                    │
│  │ ImageAnalyzer   │────▶│ Content Check   │                                    │
│  │                 │     │ (moderation)    │                                    │
│  │ - Claude Vision │     └────────┬────────┘                                    │
│  │ - Voyage Embed  │              │                                              │
│  └────────┬────────┘              │                                              │
│           │                       │ If unsafe:                                   │
│           │                       ▼                                              │
│           │              ┌─────────────────┐                                    │
│           │              │ ModerationAction│                                    │
│           │              │ - Delete msg    │                                    │
│           │              │ - Warn user     │                                    │
│           │              │ - Notify admins │                                    │
│           │              │ - Log (no save) │                                    │
│           │              └─────────────────┘                                    │
│           │                                                                      │
│           │ If safe:                                                             │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ ImageStorage    │ ─── Upload to DO Spaces, get permanent URL                 │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ Database Insert │ ─── Create image_observation record                        │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ BuildClusterer  │ ─── Assign to existing cluster or create new              │
│  │                 │                                                            │
│  │ - Compare to    │                                                            │
│  │   centroids     │                                                            │
│  │ - Check recency │                                                            │
│  │ - Update/create │                                                            │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ BuildNarrator   │ ─── (On retrieval) Generate progression narrative         │
│  └─────────────────┘                                                            │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Image Analysis

### 4.1 Analyzer Configuration

```python
# src/memory/images/analyzer.py

from dataclasses import dataclass

@dataclass
class ImageAnalysisConfig:
    # Claude model for vision
    vision_model: str = "claude-sonnet-4-5-20250929"
    
    # Voyage model for embeddings
    embedding_model: str = "voyage-multimodal-3"
    embedding_dimensions: int = 1024
    
    # Analysis settings
    max_image_size_mb: int = 10
    supported_formats: tuple = ("png", "jpg", "jpeg", "gif", "webp")
    
    # Moderation thresholds
    nsfw_threshold: float = 0.7          # Flag if confidence > this
    violence_threshold: float = 0.8
    require_human_review: float = 0.5    # Uncertain range: flag but don't auto-delete
```

### 4.2 Analysis Prompt

```python
IMAGE_ANALYSIS_PROMPT = """
You are analyzing a Minecraft screenshot shared in a Discord community.

## Task
Analyze this image and provide:
1. A detailed description of what you see
2. A one-line summary suitable for search/retrieval
3. Relevant tags for categorization
4. Structured element detection
5. An observation type classification

## Output Format
Return a JSON object with these fields:

```json
{
  "description": "Detailed 2-3 sentence description of the image",
  "summary": "One-line summary (under 100 chars)",
  "tags": ["tag1", "tag2", "tag3"],
  "detected_elements": {
    "biome": "plains|forest|desert|nether|end|ocean|mountain|swamp|other",
    "time_of_day": "day|night|sunset|sunrise|unknown",
    "structures": ["tower", "wall", "house", "farm", "bridge", ...],
    "materials": ["stone", "wood", "glass", "concrete", ...],
    "style": "medieval|modern|rustic|futuristic|organic|other",
    "completion_stage": "foundation|early|mid|late|complete|unknown"
  },
  "observation_type": "build_progress|landscape|redstone|farm|other"
}
```

## Guidelines
- Focus on Minecraft-specific elements (blocks, structures, biomes)
- Note architectural style and building techniques
- Identify the apparent stage of construction if it's a build
- Be specific about materials and design choices
- If this appears to be a continuation of a previous build, note distinguishing features

## Example

For an image showing a half-built stone castle with towers:

```json
{
  "description": "A medieval-style castle under construction in a plains biome. Two corner towers are complete with crenellations, connected by partially-built curtain walls. The foundation suggests a large central keep is planned. Stone brick is the primary material with oak wood accents.",
  "summary": "Medieval castle with two towers, walls in progress",
  "tags": ["castle", "medieval", "stone_brick", "towers", "construction"],
  "detected_elements": {
    "biome": "plains",
    "time_of_day": "day",
    "structures": ["tower", "wall", "foundation"],
    "materials": ["stone_brick", "oak_wood", "cobblestone"],
    "style": "medieval",
    "completion_stage": "mid"
  },
  "observation_type": "build_progress"
}
```

Analyze the provided image:
"""
```

### 4.3 Content Moderation Prompt

```python
CONTENT_MODERATION_PROMPT = """
You are a content moderation system. Analyze this image for policy violations.

## Check For
1. **NSFW content**: Nudity, sexual content, suggestive imagery
2. **Violence**: Gore, graphic violence, harm to people/animals
3. **Illegal content**: Drug use, weapons in threatening context, CSAM indicators
4. **Harassment**: Targeted harassment, doxxing, personal information exposure
5. **Spam/Scam**: Phishing, scam content, malicious links in screenshots

## Output Format
Return JSON:

```json
{
  "is_safe": true|false,
  "confidence": 0.0-1.0,
  "flags": [],
  "violation_type": null|"nsfw"|"violence"|"illegal"|"harassment"|"spam",
  "description": "Brief description of violation if any, or 'No policy violations detected'"
}
```

## Guidelines
- Minecraft violence (combat, mobs) is ALLOWED
- Pixel art should be evaluated for content, not dismissed as "just pixels"
- When uncertain, flag for review rather than auto-approving
- Provide enough description for human moderators to understand without seeing the image

Analyze this image:
"""
```

### 4.4 Implementation

```python
# src/memory/images/analyzer.py

import base64
import hashlib
from dataclasses import dataclass
from typing import Optional
import json

from anthropic import AsyncAnthropic
import voyageai

from .config import ImageAnalysisConfig

@dataclass
class AnalysisResult:
    description: str
    summary: str
    tags: list[str]
    detected_elements: dict
    observation_type: str
    embedding: list[float]
    file_hash: str

@dataclass
class ModerationResult:
    is_safe: bool
    confidence: float
    flags: list[str]
    violation_type: Optional[str]
    description: str

class ImageAnalyzer:
    def __init__(
        self, 
        anthropic_client: AsyncAnthropic,
        voyage_client: voyageai.AsyncClient,
        config: Optional[ImageAnalysisConfig] = None
    ):
        self.anthropic = anthropic_client
        self.voyage = voyage_client
        self.config = config or ImageAnalysisConfig()
    
    async def analyze(self, image_bytes: bytes, media_type: str) -> AnalysisResult:
        """Full analysis: description, tags, elements, embedding."""
        
        # Generate file hash for deduplication
        file_hash = hashlib.sha256(image_bytes).hexdigest()
        
        # Get Claude vision analysis
        analysis = await self._get_vision_analysis(image_bytes, media_type)
        
        # Get Voyage multimodal embedding
        embedding = await self._get_embedding(image_bytes)
        
        return AnalysisResult(
            description=analysis["description"],
            summary=analysis["summary"],
            tags=analysis["tags"],
            detected_elements=analysis["detected_elements"],
            observation_type=analysis["observation_type"],
            embedding=embedding,
            file_hash=file_hash
        )
    
    async def moderate(self, image_bytes: bytes, media_type: str) -> ModerationResult:
        """Check image for policy violations."""
        
        base64_image = base64.standard_b64encode(image_bytes).decode("utf-8")
        
        response = await self.anthropic.messages.create(
            model=self.config.vision_model,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_image
                        }
                    },
                    {
                        "type": "text",
                        "text": CONTENT_MODERATION_PROMPT
                    }
                ]
            }]
        )
        
        result = self._parse_json_response(response.content[0].text)
        
        return ModerationResult(
            is_safe=result.get("is_safe", False),
            confidence=result.get("confidence", 0.0),
            flags=result.get("flags", []),
            violation_type=result.get("violation_type"),
            description=result.get("description", "Analysis failed")
        )
    
    async def _get_vision_analysis(self, image_bytes: bytes, media_type: str) -> dict:
        """Get structured analysis from Claude Vision."""
        
        base64_image = base64.standard_b64encode(image_bytes).hexdigest()
        
        response = await self.anthropic.messages.create(
            model=self.config.vision_model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_image
                        }
                    },
                    {
                        "type": "text", 
                        "text": IMAGE_ANALYSIS_PROMPT
                    }
                ]
            }]
        )
        
        return self._parse_json_response(response.content[0].text)
    
    async def _get_embedding(self, image_bytes: bytes) -> list[float]:
        """Get Voyage multimodal embedding for the image."""
        
        # Voyage multimodal accepts base64 images
        base64_image = base64.standard_b64encode(image_bytes).decode("utf-8")
        
        result = await self.voyage.multimodal_embed(
            inputs=[[{"type": "image", "data": base64_image}]],
            model=self.config.embedding_model
        )
        
        return result.embeddings[0]
    
    def _parse_json_response(self, response_text: str) -> dict:
        """Extract JSON from Claude response."""
        text = response_text.strip()
        
        # Handle markdown code blocks
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {}
```

---

## 5. Build Clustering

### 5.1 Clustering Algorithm

The clustering algorithm groups observations into builds based on:
1. **Semantic similarity**: Embedding distance to existing cluster centroids
2. **Temporal proximity**: Recent activity in the cluster
3. **Context signals**: User's accompanying text, channel context

```python
# src/memory/images/clusterer.py

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
import asyncpg

@dataclass
class ClusterConfig:
    # Similarity thresholds
    assignment_threshold: float = 0.72      # Min similarity to assign to existing cluster
    new_cluster_threshold: float = 0.65     # Below this, definitely new cluster
    
    # Temporal settings
    active_window_days: int = 30            # Cluster considered "active" if updated within
    stale_window_days: int = 90             # Mark as "abandoned" if no activity
    
    # Cluster limits
    max_clusters_per_user: int = 50         # Prevent unbounded growth
    min_observations_for_cluster: int = 1   # Single image can start a cluster

@dataclass 
class ClusterAssignment:
    cluster_id: int
    is_new_cluster: bool
    similarity_score: Optional[float]
    cluster_name: str

class BuildClusterer:
    def __init__(self, db_pool: asyncpg.Pool, config: Optional[ClusterConfig] = None):
        self.db = db_pool
        self.config = config or ClusterConfig()
    
    async def assign_to_cluster(
        self,
        user_id: int,
        observation_id: int,
        embedding: list[float],
        observation_type: str,
        tags: list[str],
        privacy_level: str,
        guild_id: Optional[int]
    ) -> ClusterAssignment:
        """Assign an observation to an existing or new cluster."""
        
        # Find candidate clusters (active, same privacy scope)
        candidates = await self._find_candidate_clusters(
            user_id, privacy_level, guild_id
        )
        
        if not candidates:
            # No existing clusters - create new
            return await self._create_cluster(
                user_id, observation_id, embedding, observation_type, 
                tags, privacy_level, guild_id
            )
        
        # Find best matching cluster
        best_match = await self._find_best_match(embedding, candidates)
        
        if best_match and best_match["similarity"] >= self.config.assignment_threshold:
            # Assign to existing cluster
            await self._add_to_cluster(
                best_match["id"], observation_id, embedding
            )
            return ClusterAssignment(
                cluster_id=best_match["id"],
                is_new_cluster=False,
                similarity_score=best_match["similarity"],
                cluster_name=best_match["auto_name"]
            )
        else:
            # Create new cluster
            return await self._create_cluster(
                user_id, observation_id, embedding, observation_type,
                tags, privacy_level, guild_id
            )
    
    async def _find_candidate_clusters(
        self, user_id: int, privacy_level: str, guild_id: Optional[int]
    ) -> list[dict]:
        """Find clusters that could potentially match (active, compatible privacy)."""
        
        cutoff = datetime.utcnow() - timedelta(days=self.config.active_window_days)
        
        # Privacy-compatible: same or less restrictive origin
        if privacy_level == 'dm':
            privacy_filter = "privacy_level = 'dm'"
        elif privacy_level == 'channel_restricted':
            privacy_filter = "privacy_level IN ('dm', 'channel_restricted')"
        else:  # guild_public or global
            privacy_filter = f"(privacy_level IN ('guild_public', 'global') AND origin_guild_id = {guild_id})"
        
        sql = f"""
            SELECT id, auto_name, centroid_embedding, observation_count, last_observation_at
            FROM build_clusters
            WHERE user_id = $1
              AND status = 'active'
              AND last_observation_at > $2
              AND {privacy_filter}
            ORDER BY last_observation_at DESC
            LIMIT 20
        """
        
        rows = await self.db.fetch(sql, user_id, cutoff)
        return [dict(r) for r in rows]
    
    async def _find_best_match(
        self, embedding: list[float], candidates: list[dict]
    ) -> Optional[dict]:
        """Find the best matching cluster by cosine similarity."""
        
        if not candidates:
            return None
        
        embedding_array = np.array(embedding)
        best_match = None
        best_similarity = -1
        
        for candidate in candidates:
            if candidate["centroid_embedding"] is None:
                continue
                
            centroid = np.array(candidate["centroid_embedding"])
            similarity = np.dot(embedding_array, centroid) / (
                np.linalg.norm(embedding_array) * np.linalg.norm(centroid)
            )
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = {**candidate, "similarity": float(similarity)}
        
        return best_match
    
    async def _create_cluster(
        self, user_id: int, observation_id: int, embedding: list[float],
        observation_type: str, tags: list[str], privacy_level: str, 
        guild_id: Optional[int]
    ) -> ClusterAssignment:
        """Create a new cluster for this observation."""
        
        # Generate auto-name from tags and type
        auto_name = self._generate_cluster_name(observation_type, tags)
        
        # Insert cluster
        row = await self.db.fetchrow(
            """
            INSERT INTO build_clusters (
                user_id, auto_name, centroid_embedding, build_type, style_tags,
                observation_count, privacy_level, origin_guild_id,
                first_observation_at, last_observation_at
            ) VALUES ($1, $2, $3, $4, $5, 1, $6, $7, NOW(), NOW())
            RETURNING id
            """,
            user_id, auto_name, embedding, observation_type, tags,
            privacy_level, guild_id
        )
        
        cluster_id = row["id"]
        
        # Link observation to cluster
        await self.db.execute(
            "UPDATE image_observations SET build_cluster_id = $1 WHERE id = $2",
            cluster_id, observation_id
        )
        
        return ClusterAssignment(
            cluster_id=cluster_id,
            is_new_cluster=True,
            similarity_score=None,
            cluster_name=auto_name
        )
    
    async def _add_to_cluster(
        self, cluster_id: int, observation_id: int, embedding: list[float]
    ):
        """Add observation to existing cluster and update centroid."""
        
        # Link observation
        await self.db.execute(
            "UPDATE image_observations SET build_cluster_id = $1 WHERE id = $2",
            cluster_id, observation_id
        )
        
        # Update cluster: increment count, update timestamps, recalculate centroid
        # For efficiency, use rolling average instead of full recalculation
        await self.db.execute(
            """
            UPDATE build_clusters SET
                observation_count = observation_count + 1,
                last_observation_at = NOW(),
                updated_at = NOW(),
                -- Rolling average centroid update
                centroid_embedding = (
                    (centroid_embedding * observation_count + $2::vector) / (observation_count + 1)
                )
            WHERE id = $1
            """,
            cluster_id, embedding
        )
    
    def _generate_cluster_name(self, observation_type: str, tags: list[str]) -> str:
        """Generate a human-readable cluster name."""
        
        # Prioritize descriptive tags
        priority_tags = ["castle", "house", "tower", "farm", "bridge", "cathedral", 
                        "village", "ship", "statue", "wall", "gate", "garden"]
        
        for tag in priority_tags:
            if tag in tags:
                return f"{tag.title()} Build"
        
        # Fall back to observation type
        type_names = {
            "build_progress": "Construction Project",
            "landscape": "Landscape",
            "redstone": "Redstone Contraption", 
            "farm": "Farm Build",
            "other": "Project"
        }
        
        return type_names.get(observation_type, "Build Project")
```

### 5.2 Cluster Lifecycle

```
┌────────────────────────────────────────────────────────────────────┐
│                     Build Cluster Lifecycle                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   First Image ──▶ [ACTIVE]                                         │
│                      │                                              │
│                      │ More images within 30 days                   │
│                      ▼                                              │
│                   [ACTIVE] ◀─────────────────────┐                 │
│                      │                            │                 │
│                      │ No images for 30+ days     │ New image       │
│                      ▼                            │                 │
│                   [STALE] ────────────────────────┘                 │
│                      │                                              │
│                      │ No images for 90+ days                       │
│                      ▼                                              │
│                  [ABANDONED]                                        │
│                      │                                              │
│                      │ User shares new similar image                │
│                      ▼                                              │
│                   [ACTIVE] (reactivated)                           │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 6. Narrative Generation

### 6.1 Narrator Implementation

```python
# src/memory/images/narrator.py

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import asyncpg
from anthropic import AsyncAnthropic

@dataclass
class BuildNarrative:
    cluster_id: int
    cluster_name: str
    summary: str                    # One-paragraph summary
    timeline: list[dict]            # Chronological progression
    milestones: list[str]           # Key achievements
    current_status: str             # Where they are now
    suggested_next_steps: list[str] # Optional suggestions

class BuildNarrator:
    def __init__(self, db_pool: asyncpg.Pool, anthropic_client: AsyncAnthropic):
        self.db = db_pool
        self.anthropic = anthropic_client
    
    async def generate_narrative(
        self, cluster_id: int, max_observations: int = 10
    ) -> BuildNarrative:
        """Generate a narrative story for a build cluster."""
        
        # Fetch cluster info
        cluster = await self.db.fetchrow(
            "SELECT * FROM build_clusters WHERE id = $1", cluster_id
        )
        
        # Fetch observations in chronological order
        observations = await self.db.fetch(
            """
            SELECT id, description, summary, tags, detected_elements,
                   captured_at, accompanying_text
            FROM image_observations
            WHERE build_cluster_id = $1
            ORDER BY captured_at ASC
            LIMIT $2
            """,
            cluster_id, max_observations
        )
        
        # Build timeline
        timeline = [
            {
                "date": obs["captured_at"].isoformat(),
                "description": obs["description"],
                "stage": obs["detected_elements"].get("completion_stage", "unknown")
            }
            for obs in observations
        ]
        
        # Generate narrative via Claude
        narrative_text = await self._generate_narrative_text(cluster, observations)
        
        return BuildNarrative(
            cluster_id=cluster_id,
            cluster_name=cluster["auto_name"] or cluster["user_name"],
            summary=narrative_text["summary"],
            timeline=timeline,
            milestones=narrative_text["milestones"],
            current_status=narrative_text["current_status"],
            suggested_next_steps=narrative_text.get("suggestions", [])
        )
    
    async def _generate_narrative_text(
        self, cluster: dict, observations: list[dict]
    ) -> dict:
        """Use Claude to generate narrative text from observations."""
        
        # Format observations for prompt
        obs_text = "\n\n".join([
            f"**{obs['captured_at'].strftime('%B %d, %Y')}**\n{obs['description']}"
            for obs in observations
        ])
        
        prompt = f"""
You are helping tell the story of a Minecraft build's progression.

## Build Info
- Name: {cluster['auto_name']}
- Type: {cluster['build_type']}
- Started: {cluster['first_observation_at'].strftime('%B %d, %Y') if cluster['first_observation_at'] else 'Unknown'}
- Last Update: {cluster['last_observation_at'].strftime('%B %d, %Y') if cluster['last_observation_at'] else 'Unknown'}
- Total Snapshots: {cluster['observation_count']}

## Chronological Observations
{obs_text}

## Task
Generate a narrative summary of this build's progression. Return JSON:

```json
{{
  "summary": "2-3 sentence narrative summary of the build's journey",
  "milestones": ["Milestone 1", "Milestone 2", ...],
  "current_status": "Where the build currently stands",
  "suggestions": ["Optional suggestion 1", ...] 
}}
```

Be specific about what changed between observations. Celebrate progress!
"""
        
        response = await self.anthropic.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return self._parse_json_response(response.content[0].text)
    
    async def get_brief_context(
        self, user_id: int, query: str, privacy_level: str,
        guild_id: Optional[int], max_clusters: int = 3
    ) -> str:
        """Get brief build context for injection into chat responses."""
        
        # Build privacy filter
        if privacy_level == 'dm':
            privacy_filter = "TRUE"  # Can see all in DM
        elif privacy_level == 'channel_restricted':
            privacy_filter = f"""
                privacy_level IN ('global', 'guild_public')
                OR (privacy_level = 'channel_restricted' AND origin_guild_id = {guild_id})
            """
        else:
            privacy_filter = f"""
                privacy_level IN ('global', 'guild_public')
                AND origin_guild_id = {guild_id}
            """
        
        # Find relevant clusters
        clusters = await self.db.fetch(
            f"""
            SELECT id, auto_name, description, observation_count,
                   first_observation_at, last_observation_at, status
            FROM build_clusters
            WHERE user_id = $1 AND {privacy_filter}
            ORDER BY last_observation_at DESC
            LIMIT $2
            """,
            user_id, max_clusters
        )
        
        if not clusters:
            return ""
        
        lines = ["## User's Recent Builds"]
        for c in clusters:
            duration = ""
            if c["first_observation_at"] and c["last_observation_at"]:
                days = (c["last_observation_at"] - c["first_observation_at"]).days
                duration = f" ({days} days)"
            
            lines.append(f"- **{c['auto_name']}**: {c['observation_count']} snapshots{duration}")
            if c["description"]:
                lines.append(f"  {c['description'][:100]}...")
        
        return "\n".join(lines)
```

### 6.2 Narrative Injection

When the user asks about their builds or shares a new image, the narrative context is injected into Claude's system prompt:

```python
# In ClaudeClient.chat() or MemoryManager.retrieve()

async def _get_build_context(self, user_id: int, channel: discord.abc.Messageable) -> str:
    """Get build context for memory injection."""
    
    privacy_level = await classify_channel_privacy(channel)
    guild_id = getattr(channel, 'guild', None)
    guild_id = guild_id.id if guild_id else None
    
    return await self.narrator.get_brief_context(
        user_id, "", privacy_level.value, guild_id
    )
```

---

## 7. Content Moderation

### 7.1 Moderation Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Content Moderation Flow                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Image Received                                                             │
│        │                                                                     │
│        ▼                                                                     │
│   ┌─────────────────┐                                                       │
│   │ ImageAnalyzer.  │                                                       │
│   │ moderate()      │                                                       │
│   └────────┬────────┘                                                       │
│            │                                                                 │
│            ▼                                                                 │
│   ┌─────────────────────────────────────────────┐                           │
│   │            Confidence Check                  │                           │
│   └─────────────────────────────────────────────┘                           │
│            │                    │                    │                       │
│     confidence < 0.5     0.5 ≤ conf < 0.7     confidence ≥ 0.7              │
│     (likely safe)        (uncertain)          (likely unsafe)               │
│            │                    │                    │                       │
│            ▼                    ▼                    ▼                       │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│   │   PROCEED    │    │  FLAG FOR    │    │   ACTIVE     │                  │
│   │   NORMALLY   │    │   REVIEW     │    │  MODERATION  │                  │
│   └──────────────┘    │              │    │              │                  │
│                       │ - Log event  │    │ - Delete msg │                  │
│                       │ - Notify mod │    │ - Warn user  │                  │
│                       │ - Process    │    │ - Log event  │                  │
│                       │   image      │    │ - Notify mod │                  │
│                       └──────────────┘    │ - NO STORAGE │                  │
│                                           └──────────────┘                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Moderation Implementation

```python
# src/memory/images/observer.py (partial)

class ImageObserver:
    async def handle_image(
        self,
        message: discord.Message,
        attachment: discord.Attachment
    ) -> Optional[int]:
        """Process an image attachment. Returns observation_id if stored."""
        
        # Download image
        image_bytes = await attachment.read()
        media_type = self._get_media_type(attachment.filename)
        
        # STEP 1: Content moderation (MUST happen first)
        moderation = await self.analyzer.moderate(image_bytes, media_type)
        
        if not moderation.is_safe:
            if moderation.confidence >= 0.7:
                # High confidence violation - active moderation
                await self._handle_violation(
                    message, moderation, delete_message=True
                )
                return None
            elif moderation.confidence >= 0.5:
                # Uncertain - flag for review but still process
                await self._flag_for_review(message, moderation)
                # Continue processing...
        
        # STEP 2: If safe, proceed with analysis and storage
        analysis = await self.analyzer.analyze(image_bytes, media_type)
        
        # Check for duplicate
        existing = await self._check_duplicate(analysis.file_hash, message.author.id)
        if existing:
            return existing  # Return existing observation_id
        
        # Upload to storage
        storage_key, storage_url = await self.storage.upload(
            image_bytes, message.author.id, analysis.file_hash, media_type
        )
        
        # Get privacy level
        privacy_level = await classify_channel_privacy(message.channel)
        guild_id = message.guild.id if message.guild else None
        
        # Insert observation
        observation_id = await self._insert_observation(
            user_id=message.author.id,
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=guild_id,
            storage_key=storage_key,
            storage_url=storage_url,
            original_url=attachment.url,
            analysis=analysis,
            privacy_level=privacy_level,
            accompanying_text=message.content
        )
        
        # Assign to cluster
        await self.clusterer.assign_to_cluster(
            user_id=message.author.id,
            observation_id=observation_id,
            embedding=analysis.embedding,
            observation_type=analysis.observation_type,
            tags=analysis.tags,
            privacy_level=privacy_level.value,
            guild_id=guild_id
        )
        
        return observation_id
    
    async def _handle_violation(
        self,
        message: discord.Message,
        moderation: ModerationResult,
        delete_message: bool
    ):
        """Handle a content policy violation."""
        
        # Log to database (text description only, NO image)
        await self.db.execute(
            """
            INSERT INTO image_moderation_log (
                user_id, message_id, channel_id, guild_id,
                violation_type, violation_description, confidence,
                message_deleted, user_warned, admin_notified
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            message.author.id, message.id, message.channel.id,
            message.guild.id if message.guild else None,
            moderation.violation_type, moderation.description, moderation.confidence,
            delete_message, True, True
        )
        
        # Delete the message
        if delete_message:
            try:
                await message.delete()
            except discord.Forbidden:
                pass  # Log that we couldn't delete
        
        # Warn the user via DM
        try:
            await message.author.send(
                f"⚠️ **Content Warning**\n\n"
                f"An image you shared was flagged and removed for potentially violating "
                f"community guidelines ({moderation.violation_type}).\n\n"
                f"If you believe this was a mistake, please contact a moderator."
            )
        except discord.Forbidden:
            pass  # User has DMs disabled
        
        # Notify moderators
        await self._notify_moderators(message, moderation)
    
    async def _notify_moderators(
        self,
        message: discord.Message,
        moderation: ModerationResult
    ):
        """Send notification to moderator channel."""
        
        # This would be configured per-guild
        mod_channel_id = await self._get_mod_channel(message.guild.id)
        if not mod_channel_id:
            return
        
        mod_channel = self.bot.get_channel(mod_channel_id)
        if not mod_channel:
            return
        
        embed = discord.Embed(
            title="🚨 Image Moderation Alert",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="User", value=f"{message.author} ({message.author.id})")
        embed.add_field(name="Channel", value=f"<#{message.channel.id}>")
        embed.add_field(name="Violation Type", value=moderation.violation_type)
        embed.add_field(name="Confidence", value=f"{moderation.confidence:.0%}")
        embed.add_field(name="Description", value=moderation.description, inline=False)
        embed.set_footer(text="Image was NOT stored. This is a text-only log.")
        
        await mod_channel.send(embed=embed)
```

---

## 8. Storage

### 8.1 DigitalOcean Spaces Integration

```python
# src/memory/images/storage.py

import boto3
from botocore.config import Config
from datetime import datetime
import mimetypes

class ImageStorage:
    def __init__(
        self,
        spaces_key: str,
        spaces_secret: str,
        spaces_region: str = "nyc3",
        spaces_bucket: str = "slashai-images"
    ):
        self.bucket = spaces_bucket
        self.region = spaces_region
        
        self.client = boto3.client(
            's3',
            region_name=spaces_region,
            endpoint_url=f"https://{spaces_region}.digitaloceanspaces.com",
            aws_access_key_id=spaces_key,
            aws_secret_access_key=spaces_secret,
            config=Config(signature_version='s3v4')
        )
    
    async def upload(
        self,
        image_bytes: bytes,
        user_id: int,
        file_hash: str,
        media_type: str
    ) -> tuple[str, str]:
        """Upload image to DO Spaces. Returns (key, url)."""
        
        # Generate storage key
        ext = mimetypes.guess_extension(media_type) or ".png"
        date_prefix = datetime.utcnow().strftime("%Y/%m")
        key = f"images/{user_id}/{date_prefix}/{file_hash}{ext}"
        
        # Upload
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=image_bytes,
            ContentType=media_type,
            ACL='private'  # Not publicly accessible
        )
        
        # Generate URL (internal reference, not public)
        url = f"https://{self.bucket}.{self.region}.digitaloceanspaces.com/{key}"
        
        return key, url
    
    async def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Get a temporary signed URL for accessing an image."""
        
        return self.client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': key},
            ExpiresIn=expires_in
        )
    
    async def delete(self, key: str):
        """Delete an image from storage."""
        
        self.client.delete_object(Bucket=self.bucket, Key=key)
```

### 8.2 Storage Structure

```
slashai-images/
├── images/
│   ├── {user_id}/
│   │   ├── 2025/
│   │   │   ├── 12/
│   │   │   │   ├── abc123hash.png
│   │   │   │   ├── def456hash.jpg
│   │   │   │   └── ...
│   │   │   └── ...
│   │   └── ...
│   └── ...
```

---

## 9. Privacy Model

### 9.1 Inherited Privacy

Image observations inherit the same channel-based privacy model as text memories:

| Privacy Level | Assigned When | Observations Visible In | Cross-User |
|---------------|---------------|-------------------------|------------|
| `dm` | Image shared in DM | DMs only | No |
| `channel_restricted` | Image in role-gated channel | Same channel only | No |
| `guild_public` | Image in public channel | Any channel in same guild | **Yes** |
| `global` | N/A (images are never auto-promoted) | Everywhere | No |

**Cross-user sharing**: `guild_public` image observations and build clusters are shared across all users in the same guild. When User A asks about builds, they can see User B's publicly-shared build progress. This enables shared knowledge about community builds and projects.

**Key difference from text**: Images are **never** promoted to `global` automatically. Even explicit facts like "this is my IGN" shown in a screenshot stay at their channel privacy level.

### 9.2 Build Cluster Privacy

A build cluster's privacy level is the **most restrictive** of all its member observations:

```python
async def _update_cluster_privacy(self, cluster_id: int):
    """Update cluster privacy to most restrictive of all members."""
    
    await self.db.execute(
        """
        UPDATE build_clusters bc SET
            privacy_level = (
                SELECT CASE
                    WHEN EXISTS (
                        SELECT 1 FROM image_observations io 
                        WHERE io.build_cluster_id = bc.id AND io.privacy_level = 'dm'
                    ) THEN 'dm'
                    WHEN EXISTS (
                        SELECT 1 FROM image_observations io 
                        WHERE io.build_cluster_id = bc.id AND io.privacy_level = 'channel_restricted'
                    ) THEN 'channel_restricted'
                    ELSE 'guild_public'
                END
            )
        WHERE bc.id = $1
        """,
        cluster_id
    )
```

### 9.3 Retrieval Privacy Filter

```sql
-- Example: Retrieving build context in a public channel (cross-user)
-- User's global clusters + ANY user's guild_public clusters from same guild
SELECT bc.*, COUNT(io.id) as observation_count
FROM build_clusters bc
LEFT JOIN image_observations io ON io.build_cluster_id = bc.id
WHERE bc.status = 'active'
  AND (
    (bc.user_id = $1 AND bc.privacy_level = 'global')
    OR (bc.privacy_level = 'guild_public' AND bc.origin_guild_id = $2)
  )
GROUP BY bc.id
ORDER BY bc.last_observation_at DESC;
```

Note: The query no longer filters by `user_id` for `guild_public` content, enabling cross-user visibility of publicly-shared builds within the same guild.

---

## 10. Integration with Chat

### 10.1 Automatic Image Processing

In `discord_bot.py`:

```python
async def on_message(self, message: discord.Message):
    # Existing chat handling...
    
    # Check for image attachments
    for attachment in message.attachments:
        if self._is_supported_image(attachment):
            await self.image_observer.handle_image(message, attachment)
    
    # Continue with normal message handling...
```

### 10.2 Memory Injection

When user asks about builds or shares an image, inject build context:

```python
# In ClaudeClient.chat() or wherever context is assembled

async def _build_context(self, user_id: int, channel: discord.abc.Messageable) -> str:
    """Assemble full context including builds."""
    
    contexts = []
    
    # Text memories (existing)
    if self.memory:
        text_memories = await self.memory.retrieve(user_id, query, channel)
        if text_memories:
            contexts.append(self._format_memories(text_memories))
    
    # Build context (new)
    if self.image_memory:
        build_context = await self.image_memory.narrator.get_brief_context(
            user_id, query, privacy_level.value, guild_id
        )
        if build_context:
            contexts.append(build_context)
    
    return "\n\n".join(contexts)
```

### 10.3 Example Interactions

**User shares image with no text:**
```
User: [image of half-built castle]
slashAI: Nice progress on the castle! The towers are really taking shape—those 
crenellations look clean. Are you planning to add a gatehouse to that front wall?
```

**User asks about their builds:**
```
User: How's my castle coming along?
slashAI: Your medieval castle has come a long way since you started on December 15th. 
You've got the two corner towers complete with that great stonework, and the curtain 
walls are about half done. Last time I saw it, you were working on the foundation 
for what looks like a central keep. The stone brick and oak combo is working really 
well—very classic medieval aesthetic.
```

**Returning after a break:**
```
User: [shares new image of same castle, now with a completed keep]
slashAI: Whoa, you finished the keep! That's a huge milestone. The proportions look 
great—it really anchors the whole structure now. I can see you kept the same stone 
brick palette. What's next, the interior?
```

---

## 11. Cost Analysis

### 11.1 Per-Image Costs

| Operation | Service | Cost per Image |
|-----------|---------|----------------|
| Content moderation | Claude Sonnet (vision) | ~$0.003 |
| Image analysis | Claude Sonnet (vision) | ~$0.005 |
| Embedding | Voyage multimodal-3 | ~$0.0001 |
| Storage | DO Spaces ($0.02/GB) | ~$0.00002 (100KB avg) |

**Total per image**: ~$0.008

### 11.2 Monthly Estimates

| Usage Level | Images/Month | Estimated Cost |
|-------------|--------------|----------------|
| Light (10 users, 5 imgs/user) | 50 | ~$0.40 |
| Medium (50 users, 10 imgs/user) | 500 | ~$4.00 |
| Heavy (100 users, 20 imgs/user) | 2000 | ~$16.00 |

### 11.3 Storage Growth

- Average image size: ~100KB (Minecraft screenshots compress well)
- 1000 images ≈ 100MB
- DO Spaces: $5/mo for 250GB = room for ~2.5M images

---

## 12. Migration Plan

### 12.1 Phase 1: Schema (Day 1)
1. Run migrations 004, 005, 006
2. Configure DO Spaces bucket
3. Add environment variables

### 12.2 Phase 2: Core Pipeline (Days 2-3)
1. Implement `ImageAnalyzer` with moderation
2. Implement `ImageStorage`
3. Implement `ImageObserver`
4. Unit tests for each component

### 12.3 Phase 3: Clustering (Days 4-5)
1. Implement `BuildClusterer`
2. Implement `BuildNarrator`
3. Integration tests

### 12.4 Phase 4: Integration (Days 6-7)
1. Hook into `on_message` for image detection
2. Integrate with memory retrieval
3. End-to-end testing

### 12.5 Phase 5: Deployment (Day 8)
1. Update `requirements.txt` (add `boto3`, `voyageai`)
2. Update `.do/app.yaml` with new env vars
3. Deploy to staging
4. Deploy to production

---

## 13. Environment Variables

```bash
# Existing
DATABASE_URL=postgresql://...
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...

# New for image memory
DO_SPACES_KEY=...
DO_SPACES_SECRET=...
DO_SPACES_REGION=nyc3
DO_SPACES_BUCKET=slashai-images

# Moderation config
IMAGE_MODERATION_ENABLED=true
MOD_CHANNEL_ID=123456789  # Per-guild in production
```

---

## 14. Open Questions

1. **Cluster naming**: Auto-generate vs. let users name their builds via command?
2. **Milestone detection**: Should we auto-detect milestones (completion stages) and notify?
3. **Cross-referencing**: When narrating, should we fetch and re-analyze stored images, or rely on saved descriptions?
4. **Retention**: How long to keep images? Indefinitely? User-controllable?
5. **MCP exposure**: Should Claude Code be able to query build history? Privacy implications?

---

## 15. Future Enhancements (v0.9.3+)

- User commands: `/builds`, `/myprojects`, `/forget-build`
- Automatic milestone notifications ("Your castle just hit 10 snapshots!")
- Build comparison ("How does my castle compare to last month?")
- Community features (opt-in build showcases, inspiration gallery)
- Time-lapse generation (stitch observations into progression video)
- Style transfer suggestions ("Based on your medieval builds, you might like...")
