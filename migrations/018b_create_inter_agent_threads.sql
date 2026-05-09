-- Migration 018b: Create inter_agent_threads table for Enhancement 015
-- Tracks bot-to-bot conversation lifecycle so we can enforce turn caps,
-- engagement-probability decay, and human-interrupt-wins semantics.
-- Lands in v0.14.0; activated in v0.14.3.

CREATE TABLE inter_agent_threads (
    id BIGSERIAL PRIMARY KEY,

    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    initiator_persona_id TEXT NOT NULL,
    -- Participants is JSONB so future threads can include >2 personas
    participants JSONB NOT NULL,         -- e.g. ["slashai", "lena"]

    turn_count INT NOT NULL DEFAULT 0,
    max_turns INT NOT NULL DEFAULT 4,

    -- The seed: what triggered this thread
    seed_message_id BIGINT,              -- if reacting to a human message
    seed_topic TEXT,                     -- if cold-start (heartbeat new_topic into engage_persona)

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_turn_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    ended_reason TEXT                    -- 'turn_cap', 'human_interrupt', 'natural_end', 'budget_exhausted', 'superseded'
);

-- At most one active thread per channel; partial index makes the lookup cheap.
CREATE INDEX idx_threads_active
    ON inter_agent_threads(channel_id)
    WHERE ended_at IS NULL;

CREATE INDEX idx_threads_participants
    ON inter_agent_threads
    USING GIN(participants);

CREATE INDEX idx_threads_started
    ON inter_agent_threads(started_at DESC);

COMMENT ON TABLE inter_agent_threads IS 'Bot-to-bot conversation lifecycle (Enhancement 015 / v0.14.3)';
COMMENT ON COLUMN inter_agent_threads.participants IS 'JSONB array of persona IDs participating in the thread';
COMMENT ON COLUMN inter_agent_threads.ended_reason IS 'turn_cap | human_interrupt | natural_end | budget_exhausted | superseded';
