-- Migration 004: Add indexes for performance
-- Run after data is loaded for optimal IVFFlat training

-- Basic indexes for common queries
CREATE INDEX memories_user_id_idx ON memories(user_id);
CREATE INDEX memories_type_idx ON memories(memory_type);
CREATE INDEX memories_updated_idx ON memories(updated_at DESC);

-- Index for privacy-filtered retrieval (critical for performance)
CREATE INDEX memories_privacy_idx ON memories(user_id, privacy_level, origin_guild_id, origin_channel_id);

-- Vector similarity search index (IVFFlat for <1M rows)
-- Note: lists=100 is appropriate for up to ~100k rows
CREATE INDEX memories_embedding_idx ON memories
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Prevent exact duplicate summaries per user
CREATE UNIQUE INDEX memories_user_summary_idx
    ON memories(user_id, md5(topic_summary));

-- Memory session indexes
CREATE INDEX IF NOT EXISTS memory_sessions_user_channel_idx ON memory_sessions(user_id, channel_id);
CREATE INDEX IF NOT EXISTS memory_sessions_activity_idx ON memory_sessions(last_activity_at DESC);
