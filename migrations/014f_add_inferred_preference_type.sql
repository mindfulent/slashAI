-- Migration 014f: Add inferred_preference memory type
-- Part of v0.12.5 - Bidirectional Reactor Preference Inference

-- Update the memory_type constraint to include 'inferred_preference'
ALTER TABLE memories DROP CONSTRAINT memory_type_valid;
ALTER TABLE memories ADD CONSTRAINT memory_type_valid
    CHECK (memory_type IN ('episodic', 'semantic', 'procedural', 'community_observation', 'inferred_preference'));

-- Add comment documenting the new type
COMMENT ON COLUMN memories.memory_type IS
    'Memory classification: episodic (conversation), semantic (facts), procedural (skills), community_observation (reaction-triggered from message), inferred_preference (reaction-inferred reactor preference)';
