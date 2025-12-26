-- Migration 003: Create memory_sessions table
-- Tracks conversations for extraction triggers
-- Named memory_sessions to avoid conflict with existing auth sessions table

CREATE TABLE IF NOT EXISTS memory_sessions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    -- Privacy context (captured at session start)
    channel_privacy_level TEXT NOT NULL DEFAULT 'guild_public',

    -- Session state
    message_count INT DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ DEFAULT NOW(),
    extracted_at TIMESTAMPTZ,

    -- Raw messages (JSONB array)
    messages JSONB DEFAULT '[]'::jsonb,

    UNIQUE(user_id, channel_id)
);
