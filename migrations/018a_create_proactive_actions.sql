-- Migration 018a: Create proactive_actions table for Enhancement 015
-- Audit log of every proactive decision (incl. no-ops) made by any persona.
-- Decisions are logged whether or not the actor took action so the decider
-- prompt can be tuned from real traces.

CREATE TABLE proactive_actions (
    id BIGSERIAL PRIMARY KEY,

    -- Who acted (persona ID matches personas/*.json `name`, or 'slashai' for primary)
    persona_id TEXT NOT NULL,

    -- Where
    channel_id BIGINT NOT NULL,
    guild_id BIGINT,

    -- Decision
    decision TEXT NOT NULL,              -- 'none', 'react', 'reply', 'new_topic', 'engage_persona'
    trigger TEXT NOT NULL,               -- 'activity' or 'heartbeat'

    -- Action artifacts (NULL for decision='none')
    target_message_id BIGINT,            -- message reacted/replied to
    target_persona_id TEXT,              -- persona engaged (for engage_persona / reply-to-persona)
    emoji TEXT,                          -- for decision='react'
    posted_message_id BIGINT,            -- the message we created (for reply/new_topic)
    inter_agent_thread_id BIGINT,        -- if part of a persona-to-persona thread

    -- Decider trace
    reasoning TEXT,                      -- LLM's stated reason; debug/tuning
    confidence FLOAT,                    -- 0.0-1.0 from decider
    decider_model TEXT,                  -- which model made the call

    -- Cost tracking
    input_tokens INT,
    output_tokens INT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Daily-budget queries: persona + day + non-noop
CREATE INDEX idx_proactive_persona_day
    ON proactive_actions(persona_id, created_at DESC)
    WHERE decision != 'none';

-- Per-persona-per-channel cooldown lookups
CREATE INDEX idx_proactive_persona_channel
    ON proactive_actions(persona_id, channel_id, created_at DESC);

-- Cross-persona lockout (any persona's last action in a channel)
CREATE INDEX idx_proactive_channel_recent
    ON proactive_actions(channel_id, created_at DESC)
    WHERE decision != 'none';

-- Inter-agent thread lookups
CREATE INDEX idx_proactive_thread
    ON proactive_actions(inter_agent_thread_id)
    WHERE inter_agent_thread_id IS NOT NULL;

COMMENT ON TABLE proactive_actions IS 'Audit log of proactive decisions per persona (Enhancement 015 / v0.14.0)';
COMMENT ON COLUMN proactive_actions.persona_id IS 'Persona that made the decision (matches personas/*.json name)';
COMMENT ON COLUMN proactive_actions.decision IS 'One of: none, react, reply, new_topic, engage_persona';
COMMENT ON COLUMN proactive_actions.trigger IS 'What invoked the decider: activity (on_message) or heartbeat (tasks.loop)';
COMMENT ON COLUMN proactive_actions.reasoning IS 'Decider LLM stated reason - tunable via /proactive history';
