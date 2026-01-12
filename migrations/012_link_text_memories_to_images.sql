-- Migration 012: Link text memories to image observations
-- This enables bridging textual semantic memory with visual embeddings

-- Add column to link text memories to their source images
ALTER TABLE memories
ADD COLUMN linked_image_id INT REFERENCES image_observations(id) ON DELETE SET NULL;

-- Create index for efficient lookups
CREATE INDEX idx_memories_linked_image ON memories(linked_image_id);

-- Add comment for documentation
COMMENT ON COLUMN memories.linked_image_id IS
    'References image_observations.id when this memory is a text representation of an image.
     Enables bridging text and image embedding spaces for cross-modal retrieval.';
