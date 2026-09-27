-- 019_alert_wall_clock.sql
-- ADR-063: the wall-clock moment an alert opened, for human response times.
--
-- opened_at is the SIMULATION clock (the event's own timestamp); acknowledged_at,
-- actioned_at and closed_at are WALL clock. /api/simulations/{id}/metrics subtracted one
-- from the other and reported a mean time-to-acknowledge of 20,812,517 s (241 days).
ALTER TABLE active_alerts ADD COLUMN IF NOT EXISTS opened_wall_at TIMESTAMPTZ;
