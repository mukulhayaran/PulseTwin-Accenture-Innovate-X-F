-- 014_genai_orchestration.sql
-- Multi-agent orchestration for the `incident` intent (ADR-045).
--
-- Specialist attribution must survive past the moment the UI stops reading the live
-- heartbeat and starts reading the persisted ledger (serve/routers/triage.py::get_run's
-- own comment already names this exact handover as flicker-prone). Without a persisted
-- `agent` column, a finished orchestrated run would lose which specialist made which
-- call the instant it stopped being "in flight".

ALTER TABLE triage_tool_calls ADD COLUMN IF NOT EXISTS agent TEXT;

ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS orchestrated  BOOLEAN DEFAULT FALSE;
ALTER TABLE triage_runs ADD COLUMN IF NOT EXISTS agents_used   JSONB;
