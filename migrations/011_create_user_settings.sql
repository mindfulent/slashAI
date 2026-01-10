-- Migration 011: Create user_settings table
-- Part of the Scheduled Reminders feature (v0.9.17)

CREATE TABLE IF NOT EXISTS user_settings (
    user_id BIGINT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE user_settings IS 'User preferences including timezone for reminders';
COMMENT ON COLUMN user_settings.timezone IS 'IANA timezone name (e.g., America/Los_Angeles, Europe/London)';
