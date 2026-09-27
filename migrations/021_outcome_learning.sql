-- 021_outcome_learning.sql
-- Structured operational memory (ADR-064). NOT conversation memory: every row is written
-- by deterministic code -- a human decision, an approved action applied to the simulated
-- twin, and a configuration-driven evaluation of what happened afterwards.
--
-- action_outcomes: one monitoring window per APPLIED action. The classification
--   (successful / partially_successful / no_material_effect / harmful / inconclusive /
--   cancelled) is computed from thresholds in configs/alert_policies.yaml, never by a model.
-- incident_learning_records: the join a later investigation reads -- trigger, agent run,
--   proposal, decision, experiment, outcome -- so "what happened last time" has one answer.

CREATE TABLE IF NOT EXISTS action_outcomes (
    outcome_id         TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    updated_at         TEXT,
    plant_id           TEXT NOT NULL,
    station_id         INTEGER NOT NULL,
    proposal_id        TEXT NOT NULL,
    sim_run_id         TEXT,
    experiment_id      TEXT,
    detector           TEXT,
    intervention_type  TEXT NOT NULL,
    status             TEXT NOT NULL,
    metric             TEXT NOT NULL,
    direction          TEXT NOT NULL,
    applied_at         TEXT,
    applied_seq        INTEGER,
    window_s           DOUBLE PRECISION,
    window_ends_at     TEXT,
    baseline_value     DOUBLE PRECISION,
    expected_change    DOUBLE PRECISION,
    expected_ci_low    DOUBLE PRECISION,
    expected_ci_high   DOUBLE PRECISION,
    expected_source    TEXT,
    actual_value       DOUBLE PRECISION,
    actual_change      DOUBLE PRECISION,
    prediction_error   DOUBLE PRECISION,
    n_events_before    INTEGER,
    n_events_after     INTEGER,
    thresholds         JSONB,
    observed           JSONB,
    reason             TEXT,
    evaluated_at       TEXT,
    versions           JSONB
);
CREATE INDEX IF NOT EXISTS idx_action_outcomes_plant
    ON action_outcomes (plant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_outcomes_lookup
    ON action_outcomes (plant_id, station_id, intervention_type);

CREATE TABLE IF NOT EXISTS incident_learning_records (
    record_id          TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    updated_at         TEXT,
    plant_id           TEXT NOT NULL,
    station_id         INTEGER,
    incident_type      TEXT NOT NULL,
    trigger_ref        JSONB,
    triage_run_id      TEXT,
    proposal_id        TEXT,
    decision           TEXT NOT NULL,
    decided_by_role    TEXT,
    decision_note      TEXT,
    intervention_type  TEXT,
    action             JSONB,
    experiment_id      TEXT,
    outcome_id         TEXT,
    expected           JSONB,
    baseline           JSONB,
    actual             JSONB,
    prediction_error   DOUBLE PRECISION,
    success_status     TEXT,
    confidence         TEXT,
    context            JSONB,
    simulator_version  TEXT,
    model_version      TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_lookup
    ON incident_learning_records (plant_id, incident_type, station_id);
CREATE INDEX IF NOT EXISTS idx_learning_proposal
    ON incident_learning_records (proposal_id);
