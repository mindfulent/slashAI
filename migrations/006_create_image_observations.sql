-- Migration 006: Create image_observations table
-- Individual image observations (one per shared image)

CREATE TABLE image_observations (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,

    -- Discord context
    message_id BIGINT NOT NULL UNIQUE,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    -- Storage references
    storage_key TEXT NOT NULL,
    storage_url TEXT NOT NULL,
    original_url TEXT,
    file_hash TEXT NOT NULL,
    file_size_bytes INT,
    dimensions TEXT,

    -- Visual analysis (from Claude)
    description TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT[] DEFAULT '{}',
    detected_elements JSONB DEFAULT '{}',

    -- Embedding (Voyage multimodal-3)
    embedding vector(1024) NOT NULL,

    -- Classification
    observation_type TEXT DEFAULT 'unknown',
    build_cluster_id INT REFERENCES build_clusters(id) ON DELETE SET NULL,

    -- Privacy (inherited from channel at capture time)
    privacy_level TEXT NOT NULL,

    -- Context
    accompanying_text TEXT,
    conversation_context TEXT,

    -- Timestamps
    captured_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT observation_type_valid
        CHECK (observation_type IN ('build_progress', 'landscape', 'redstone', 'farm', 'other', 'unknown')),
    CONSTRAINT observation_privacy_valid
        CHECK (privacy_level IN ('dm', 'channel_restricted', 'guild_public', 'global'))
);
