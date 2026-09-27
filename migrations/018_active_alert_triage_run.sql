-- 018_active_alert_triage_run.sql
-- The auto-investigation link on a live alert (ADR-063).
--
-- SQLite gets this column from serve/db.py::_OPS_ADDED_COLUMNS at runtime, but no Postgres
-- migration ever added it. On the Postgres stack GET /api/alerts/{unit}/status?run_id=...
-- (the supervisor's live alert panel) returned 500, and the auto-investigation's
-- `UPDATE active_alerts SET triage_run_id=...` failed inside a best-effort try, so the
-- link from an alert to its investigation was silently lost.
ALTER TABLE active_alerts ADD COLUMN IF NOT EXISTS triage_run_id TEXT;
