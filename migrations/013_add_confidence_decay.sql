-- Migration 013: Add confidence decay tracking
-- Version: v0.10.1
-- Date: 2026-01-12
--
-- Adds columns and indexes for relevance-weighted confidence decay.
-- Memories decay based on time since access AND retrieval frequency.

-- Part 1: Add decay tracking columns
ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS decay_policy TEXT DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS retrieval_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS is_protected BOOLEAN DEFAULT FALSE;

-- Part 2: Add constraint for valid decay policies
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'decay_policy_valid'
    ) THEN
        ALTER TABLE memories
            ADD CONSTRAINT decay_policy_valid
            CHECK (decay_policy IN ('none', 'standard', 'aggressive', 'pending_deletion'));
    END IF;
END $$;

-- Part 3: Set default decay policies based on memory type
-- Semantic memories don't decay, others use standard decay
UPDATE memories
SET decay_policy = CASE
    WHEN memory_type = 'semantic' THEN 'none'
    WHEN memory_type = 'procedural' THEN 'standard'
    ELSE 'standard'
END
WHERE decay_policy IS NULL OR decay_policy = 'standard';

-- Part 4: Protect high-confidence semantic memories
UPDATE memories
SET is_protected = TRUE
WHERE memory_type = 'semantic'
  AND confidence >= 0.9;

-- Part 5: Initialize retrieval_count from last_accessed_at heuristic
-- Memories that have been accessed likely have at least 1 retrieval
UPDATE memories
SET retrieval_count = 1
WHERE last_accessed_at IS NOT NULL
  AND retrieval_count = 0;

-- Part 6: Add index for decay job queries
-- Helps the background job find memories eligible for decay
CREATE INDEX IF NOT EXISTS idx_memories_decay
    ON memories(memory_type, last_accessed_at, decay_policy)
    WHERE decay_policy != 'none';

-- Part 7: Add index for consolidation candidate queries
-- Helps find frequently-accessed episodic memories for potential promotion
CREATE INDEX IF NOT EXISTS idx_memories_consolidation
    ON memories(memory_type, retrieval_count)
    WHERE memory_type = 'episodic' AND retrieval_count >= 5;

-- Part 8: Add index for protected memories
CREATE INDEX IF NOT EXISTS idx_memories_protected
    ON memories(is_protected)
    WHERE is_protected = TRUE;
