-- Migration 005: Create build_clusters table
-- Groups related image observations into build/project clusters

CREATE TABLE build_clusters (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,

    -- Cluster identity
    auto_name TEXT NOT NULL,
    user_name TEXT,
    description TEXT,

    -- Embedding (centroid of member observations)
    centroid_embedding vector(1024),

    -- Classification
    build_type TEXT DEFAULT 'unknown',
    style_tags TEXT[] DEFAULT '{}',

    -- Progression tracking
    status TEXT DEFAULT 'active',
    observation_count INT DEFAULT 0,

    -- Milestone tracking (JSONB array)
    milestones JSONB DEFAULT '[]',

    -- Privacy (most restrictive of all member observations)
    privacy_level TEXT NOT NULL,
    origin_guild_id BIGINT,

    -- Timestamps
    first_observation_at TIMESTAMPTZ,
    last_observation_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT cluster_status_valid
        CHECK (status IN ('active', 'completed', 'abandoned')),
    CONSTRAINT cluster_privacy_valid
        CHECK (privacy_level IN ('dm', 'channel_restricted', 'guild_public', 'global'))
);
