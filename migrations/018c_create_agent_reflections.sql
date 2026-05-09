-- Migration 018c: Create agent_reflections table for Enhancement 015
-- Park-style reflections about other personas, users, channels, and self.
-- Reflections cite source observations and form trees (parent_reflection_id).
-- Lands in v0.14.0; activated in v0.14.4.

CREATE TABLE agent_reflections (
    id BIGSERIAL PRIMARY KEY,

    persona_id TEXT NOT NULL,            -- the persona doing the reflecting
    subject_type TEXT NOT NULL,          -- 'persona', 'user', 'channel', 'self'
    subject_id TEXT NOT NULL,            -- 'lena', '<discord_user_id>', '<channel_id>', or persona_id for self

    content TEXT NOT NULL,               -- e.g. "Lena tends to push back when I'm being formal"
    embedding vector(1024),              -- voyage-3.5-lite, for retrieval

    importance INT NOT NULL,             -- 1-10 from Park's importance prompt
    confidence FLOAT NOT NULL DEFAULT 0.7,

    -- Provenance: which observations supported this reflection
    cites JSONB NOT NULL DEFAULT '[]',   -- e.g. [{"type": "action", "id": 123}, {"type": "message", "id": 456}]
    parent_reflection_id BIGINT REFERENCES agent_reflections(id),  -- for reflection trees

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_retrieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retrieval_count INT NOT NULL DEFAULT 0
);

CREATE INDEX idx_reflections_persona_subject
    ON agent_reflections(persona_id, subject_type, subject_id);

CREATE INDEX idx_reflections_embedding
    ON agent_reflections
    USING ivfflat (embedding vector_cosine_ops);

CREATE INDEX idx_reflections_importance
    ON agent_reflections(persona_id, importance DESC);

CREATE INDEX idx_reflections_created
    ON agent_reflections(persona_id, created_at DESC);

COMMENT ON TABLE agent_reflections IS 'Park-style reflections about subjects encountered during interaction (Enhancement 015 / v0.14.4)';
COMMENT ON COLUMN agent_reflections.subject_type IS 'persona | user | channel | self';
COMMENT ON COLUMN agent_reflections.importance IS 'Park 1-10 poignancy rating; running sum > 150 triggers next reflection';
COMMENT ON COLUMN agent_reflections.cites IS 'JSONB list of {type: "action"|"message"|"reflection", id: <pk>} for provenance';
