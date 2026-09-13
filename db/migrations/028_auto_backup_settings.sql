-- Automatic library backup settings (single row per user).
--
-- directory '' means "not configured". last_backup_at is the last *attempt*
-- time (success or failure both refresh it, so a broken directory does not
-- trigger a per-minute retry storm); last_backup_status/error carry the
-- outcome. KEEP_COUNT rotation lives in backend/auto_backup.py, not here.

CREATE TABLE IF NOT EXISTS user_auto_backup_settings (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  directory TEXT NOT NULL DEFAULT '',
  interval_kind TEXT NOT NULL DEFAULT 'daily'
    CHECK (interval_kind IN ('daily', 'every_3_days', 'weekly')),
  last_backup_at TIMESTAMPTZ,
  last_backup_status TEXT
    CHECK (last_backup_status IN ('success', 'failed')),
  last_backup_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
