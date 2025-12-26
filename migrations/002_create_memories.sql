-- Migration 002: Create memories table
-- Core memory storage with privacy levels and vector embeddings

CREATE TABLE memories (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,

    -- Topic-based storage (from RMM paper)
    topic_summary TEXT NOT NULL,
    raw_dialogue TEXT NOT NULL,
    embedding vector(1024) NOT NULL,

    -- Classification
    memory_type TEXT NOT NULL DEFAULT 'episodic',

    -- Privacy classification (see MEMORY_PRIVACY.md)
    privacy_level TEXT NOT NULL DEFAULT 'guild_public',

    -- Origin tracking (required for privacy enforcement)
    origin_channel_id BIGINT,
    origin_guild_id BIGINT,

    -- Merge tracking
    source_count INT DEFAULT 1,
    confidence FLOAT DEFAULT 1.0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ,

    -- Constraints
    CONSTRAINT privacy_level_valid
        CHECK (privacy_level IN ('dm', 'channel_restricted', 'guild_public', 'global')),
    CONSTRAINT memory_type_valid
        CHECK (memory_type IN ('episodic', 'semantic', 'procedural'))
);
