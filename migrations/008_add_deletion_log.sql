-- Migration 008: Add memory deletion log
-- v0.9.11: Audit trail for user memory deletions

-- Audit table for memory deletions
-- Captures deleted memory info for debugging and recovery
CREATE TABLE IF NOT EXISTS memory_deletion_log (
    id SERIAL PRIMARY KEY,
    memory_id INT NOT NULL,              -- Original memory ID (no FK - memory is deleted)
    user_id BIGINT NOT NULL,             -- User who owned the memory
    topic_summary TEXT NOT NULL,         -- What was deleted (for audit)
    privacy_level TEXT NOT NULL,         -- Privacy level of deleted memory
    deleted_at TIMESTAMPTZ DEFAULT NOW() -- When it was deleted
);

-- Index for querying deletion history by user
CREATE INDEX IF NOT EXISTS deletion_log_user_idx
    ON memory_deletion_log(user_id, deleted_at DESC);

-- Index for querying by original memory ID (rare, but useful for debugging)
CREATE INDEX IF NOT EXISTS deletion_log_memory_idx
    ON memory_deletion_log(memory_id);
