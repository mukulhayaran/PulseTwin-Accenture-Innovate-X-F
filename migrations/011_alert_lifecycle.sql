-- Complete, auditable alert lifecycle timestamps.
ALTER TABLE active_alerts ADD COLUMN IF NOT EXISTS actioned_at TIMESTAMPTZ;
ALTER TABLE active_alerts ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ;
ALTER TABLE active_alerts ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_active_alerts_status
    ON active_alerts (run_id, status, opened_at DESC);
