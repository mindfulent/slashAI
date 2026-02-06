-- Migration 014b: Create memory_message_links table for tracking source messages
-- Part of v0.12.0 - Reaction-Based Memory Signals

CREATE TABLE memory_message_links (
    id SERIAL PRIMARY KEY,
    memory_id INT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    -- Contribution type: 'source' (message contributed to memory), 'mentioned' (memory mentioned in message)
    contribution_type TEXT DEFAULT 'source',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_memory_message UNIQUE (memory_id, message_id)
);

-- Indexes for efficient joins
CREATE INDEX idx_memory_links_memory ON memory_message_links(memory_id);
CREATE INDEX idx_memory_links_message ON memory_message_links(message_id);
CREATE INDEX idx_memory_links_channel ON memory_message_links(channel_id);
CREATE INDEX idx_memory_links_type ON memory_message_links(contribution_type);

-- Comment for documentation
COMMENT ON TABLE memory_message_links IS 'Links memories to their source Discord messages for reaction aggregation (v0.12.0)';
COMMENT ON COLUMN memory_message_links.contribution_type IS 'How the message relates to the memory: source, mentioned';
