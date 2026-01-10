-- Migration 010: Create scheduled_reminders table
-- Part of the Scheduled Reminders feature (v0.9.17)

CREATE TABLE IF NOT EXISTS scheduled_reminders (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    cron_expression TEXT,              -- NULL for one-time reminders
    next_execution_at TIMESTAMPTZ NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    delivery_channel_id BIGINT,        -- NULL = DM
    is_channel_delivery BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'active',      -- active/paused/completed/failed
    last_executed_at TIMESTAMPTZ,
    execution_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Critical index for scheduler queries (find due reminders)
CREATE INDEX IF NOT EXISTS reminders_next_execution_idx
    ON scheduled_reminders(next_execution_at)
    WHERE status = 'active';

-- Index for user queries (list user's reminders)
CREATE INDEX IF NOT EXISTS reminders_user_idx
    ON scheduled_reminders(user_id, status);

COMMENT ON TABLE scheduled_reminders IS 'Scheduled reminders with CRON support for recurring delivery';
COMMENT ON COLUMN scheduled_reminders.cron_expression IS 'CRON expression for recurring reminders, NULL for one-time';
COMMENT ON COLUMN scheduled_reminders.status IS 'active = scheduled, paused = temporarily stopped, completed = one-time done, failed = gave up after retries';
