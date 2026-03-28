-- Migration 016: Add source_platform and user_identifier for cross-platform memory (INCEPTION Phase 3)
-- Tracks which platform a memory originated from and links Minecraft players to Discord users.

ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_platform TEXT DEFAULT 'discord';
ALTER TABLE memories ADD COLUMN IF NOT EXISTS user_identifier TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_source_platform ON memories (source_platform);
CREATE INDEX IF NOT EXISTS idx_memories_user_identifier ON memories (user_identifier)
    WHERE user_identifier IS NOT NULL;
