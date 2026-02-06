-- Migration 014a: Create message_reactions table for tracking emoji reactions
-- Part of v0.12.0 - Reaction-Based Memory Signals

CREATE TABLE message_reactions (
    id SERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,
    message_author_id BIGINT NOT NULL,
    reactor_id BIGINT NOT NULL,
    emoji TEXT NOT NULL,
    emoji_is_custom BOOLEAN DEFAULT FALSE,
    -- Emoji dimensions (from static mapping or Claude interpretation)
    sentiment FLOAT,           -- -1.0 (negative) to +1.0 (positive)
    intensity FLOAT,           -- 0.0 (mild) to 1.0 (strong)
    intent TEXT,               -- 'acknowledgment', 'agreement', 'humor', 'emphasis', etc.
    relevance TEXT,            -- 'content', 'delivery', 'author', 'meta'
    context_dependent BOOLEAN DEFAULT FALSE,  -- True if Claude should interpret
    -- Timestamps
    reacted_at TIMESTAMPTZ DEFAULT NOW(),
    removed_at TIMESTAMPTZ,    -- Soft delete: when reaction was removed
    CONSTRAINT unique_reaction UNIQUE (message_id, reactor_id, emoji)
);

-- Indexes for common query patterns
CREATE INDEX idx_reactions_message ON message_reactions(message_id);
CREATE INDEX idx_reactions_reactor ON message_reactions(reactor_id);
CREATE INDEX idx_reactions_channel ON message_reactions(channel_id);
CREATE INDEX idx_reactions_author ON message_reactions(message_author_id);
CREATE INDEX idx_reactions_guild ON message_reactions(guild_id) WHERE guild_id IS NOT NULL;
CREATE INDEX idx_reactions_active ON message_reactions(message_id) WHERE removed_at IS NULL;
CREATE INDEX idx_reactions_sentiment ON message_reactions(sentiment) WHERE sentiment IS NOT NULL;
CREATE INDEX idx_reactions_reacted_at ON message_reactions(reacted_at);

-- Comment for documentation
COMMENT ON TABLE message_reactions IS 'Tracks emoji reactions on messages for memory system integration (v0.12.0)';
COMMENT ON COLUMN message_reactions.sentiment IS 'Emotional valence: -1.0 (negative) to +1.0 (positive)';
COMMENT ON COLUMN message_reactions.intensity IS 'Emotional strength: 0.0 (mild) to 1.0 (strong)';
COMMENT ON COLUMN message_reactions.intent IS 'Purpose: acknowledgment, agreement, disagreement, humor, emphasis, question, celebration, support, warning, bookmark';
COMMENT ON COLUMN message_reactions.relevance IS 'What the reaction targets: content, delivery, author, meta';
COMMENT ON COLUMN message_reactions.context_dependent IS 'True if meaning depends on conversation context (unknown emoji)';
