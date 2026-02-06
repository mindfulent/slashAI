-- Migration 014c: Add reaction metadata columns to memories table
-- Part of v0.12.0 - Reaction-Based Memory Signals

-- Add reaction summary JSONB column for aggregated reaction data
-- Structure: {
--   "total_reactions": int,
--   "unique_reactors": int,
--   "sentiment_score": float,      -- Weighted average sentiment
--   "intensity_score": float,      -- Weighted average intensity
--   "controversy_score": float,    -- 0.0-1.0, high if mixed sentiment
--   "intent_distribution": {       -- Count by intent type
--     "agreement": int,
--     "humor": int,
--     ...
--   },
--   "top_emoji": [                 -- Most common emoji
--     {"emoji": "🔥", "count": 5},
--     ...
--   ],
--   "last_aggregated_at": timestamp
-- }
ALTER TABLE memories ADD COLUMN IF NOT EXISTS reaction_summary JSONB;

-- Confidence boost derived from reactions (-0.1 to +0.2)
-- Applied additively to base confidence during retrieval/decay
ALTER TABLE memories ADD COLUMN IF NOT EXISTS reaction_confidence_boost FLOAT DEFAULT 0.0;

-- Index for querying memories by reaction engagement
CREATE INDEX idx_memories_reaction_boost ON memories(reaction_confidence_boost)
    WHERE reaction_confidence_boost != 0.0;

-- Partial index for memories with any reactions
CREATE INDEX idx_memories_has_reactions ON memories((reaction_summary IS NOT NULL))
    WHERE reaction_summary IS NOT NULL;

-- Comment for documentation
COMMENT ON COLUMN memories.reaction_summary IS 'Aggregated reaction data from linked messages (v0.12.0)';
COMMENT ON COLUMN memories.reaction_confidence_boost IS 'Confidence adjustment from reactions: -0.1 to +0.2 (v0.12.0)';
