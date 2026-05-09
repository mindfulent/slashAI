-- Migration 018d: Add importance column to proactive_actions
-- Used by the reflection job (Enhancement 015 / v0.16.4) to score
-- proactive_actions retroactively in batches. NULL = unscored.
-- The Park threshold (sum >= 150) is computed from non-NULL rows since
-- the persona's last reflection.

ALTER TABLE proactive_actions ADD COLUMN IF NOT EXISTS importance INT;

CREATE INDEX IF NOT EXISTS idx_proactive_unscored
    ON proactive_actions(persona_id, created_at)
    WHERE importance IS NULL AND decision != 'none';

CREATE INDEX IF NOT EXISTS idx_proactive_importance_sum
    ON proactive_actions(persona_id, created_at DESC)
    WHERE importance IS NOT NULL;

COMMENT ON COLUMN proactive_actions.importance IS 'Park-style 1-10 poignancy rating, scored by the reflection job. NULL = unscored.';
