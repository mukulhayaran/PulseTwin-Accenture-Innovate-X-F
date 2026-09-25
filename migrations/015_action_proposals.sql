-- 015_action_proposals.sql
-- Proactive watch -> agent -> proposal -> human decision (ADR-051).
--
-- One row per watch firing. The agent's proposed actions are read from the linked
-- triage run's VERIFIED finding rather than copied here, so there is one source of truth
-- for what the agent proposed; this row records only what a human decided and what the
-- twin then observed.

CREATE TABLE IF NOT EXISTS action_proposals (
    proposal_id      TEXT PRIMARY KEY,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    sim_run_id       TEXT NOT NULL,
    plant_id         TEXT NOT NULL,
    detector         TEXT NOT NULL,
    station_id       INTEGER NOT NULL,
    fired_at         TEXT NOT NULL,
    trigger_evidence JSONB NOT NULL,
    triage_run_id    TEXT,
    status           TEXT NOT NULL DEFAULT 'OPEN',
    selected_action  JSONB,
    decided_by_role  TEXT,
    decided_at       TEXT,
    decision_note    TEXT,
    applied_at       TEXT,
    effective_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_proposals_plant
    ON action_proposals (plant_id, created_at DESC);
