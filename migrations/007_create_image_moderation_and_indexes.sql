-- Migration 007: Create image_moderation_log table and image-related indexes

-- Moderation log (text description only, no image storage for violations)
CREATE TABLE image_moderation_log (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,

    -- Discord context
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    -- Moderation details
    violation_type TEXT NOT NULL,
    violation_description TEXT NOT NULL,
    confidence FLOAT NOT NULL,

    -- Actions taken
    message_deleted BOOLEAN DEFAULT FALSE,
    user_warned BOOLEAN DEFAULT FALSE,
    admin_notified BOOLEAN DEFAULT FALSE,

    -- Timestamps
    detected_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT violation_type_valid
        CHECK (violation_type IN ('nsfw', 'violence', 'illegal', 'harassment', 'spam', 'other'))
);

-- Moderation log indexes
CREATE INDEX mod_log_user_idx ON image_moderation_log(user_id);
CREATE INDEX mod_log_guild_idx ON image_moderation_log(guild_id);

-- Build cluster indexes
CREATE INDEX cluster_user_idx ON build_clusters(user_id);
CREATE INDEX cluster_status_idx ON build_clusters(user_id, status);
CREATE INDEX cluster_privacy_idx ON build_clusters(user_id, privacy_level, origin_guild_id);
CREATE INDEX cluster_updated_idx ON build_clusters(updated_at DESC);

-- Cluster centroid vector index
CREATE INDEX cluster_centroid_idx ON build_clusters
    USING ivfflat (centroid_embedding vector_cosine_ops)
    WITH (lists = 50);

-- Image observation indexes
CREATE INDEX obs_user_id_idx ON image_observations(user_id);
CREATE INDEX obs_cluster_idx ON image_observations(build_cluster_id);
CREATE INDEX obs_captured_idx ON image_observations(captured_at DESC);
CREATE INDEX obs_privacy_idx ON image_observations(user_id, privacy_level, guild_id, channel_id);

-- Observation embedding vector index
CREATE INDEX obs_embedding_idx ON image_observations
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Deduplication index (prevent storing same image twice for same user)
CREATE UNIQUE INDEX obs_user_hash_idx ON image_observations(user_id, file_hash);
