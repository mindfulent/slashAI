-- Migration 009: Create analytics events table
-- Lightweight event tracking for usage metrics and performance monitoring

CREATE TABLE analytics_events (
    id BIGSERIAL PRIMARY KEY,

    -- Event identification
    event_name TEXT NOT NULL,
    event_category TEXT NOT NULL,

    -- Context (nullable for system events)
    user_id BIGINT,
    channel_id BIGINT,
    guild_id BIGINT,

    -- Flexible event data
    properties JSONB DEFAULT '{}'::jsonb,

    -- Timing
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    CONSTRAINT event_category_valid
        CHECK (event_category IN ('message', 'memory', 'command', 'tool', 'api', 'error', 'system'))
);

-- Indexes for common query patterns
CREATE INDEX idx_events_created_at ON analytics_events (created_at DESC);
CREATE INDEX idx_events_name ON analytics_events (event_name);
CREATE INDEX idx_events_category ON analytics_events (event_category);
CREATE INDEX idx_events_user_id ON analytics_events (user_id) WHERE user_id IS NOT NULL;

-- Composite index for time-range queries by category
CREATE INDEX idx_events_category_time ON analytics_events (event_category, created_at DESC);

-- GIN index for JSONB property queries
CREATE INDEX idx_events_properties ON analytics_events USING GIN (properties);
