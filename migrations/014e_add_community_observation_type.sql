-- Migration 014e: Add community_observation memory type
-- Part of v0.12.4 - Reaction-Triggered Passive Observation

-- Update the memory_type constraint to include 'community_observation'
ALTER TABLE memories DROP CONSTRAINT memory_type_valid;
ALTER TABLE memories ADD CONSTRAINT memory_type_valid
    CHECK (memory_type IN ('episodic', 'semantic', 'procedural', 'community_observation'));

-- Make embedding nullable for community_observation memories
-- This allows graceful degradation when embedding service is unavailable
ALTER TABLE memories ALTER COLUMN embedding DROP NOT NULL;

-- Add comment documenting the new type
COMMENT ON COLUMN memories.memory_type IS
    'Memory classification: episodic (conversation), semantic (facts), procedural (skills), community_observation (reaction-triggered)';
