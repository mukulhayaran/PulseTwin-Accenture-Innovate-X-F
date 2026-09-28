-- 022_incident_commander.sql
-- The Incident Commander (ADR-067): persistent incident state, investigation steps,
-- evidence, recommendations, autonomy decisions, enterprise actions, case memory and
-- agent-run observability. Every row is written by deterministic server code; an LLM
-- planner can only PROPOSE a plan that the server validates before anything runs.
--
-- Mirrored in SQLite dialect in serve/db.py::_OPS_SCHEMA_SQLITE (the test backend).
-- Timestamps are ISO-8601 TEXT, as in 020/021, so both backends compare them identically.

CREATE TABLE IF NOT EXISTS incidents (
    incident_id   TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    plant_id      TEXT NOT NULL,
    sim_run_id    TEXT,
    kind          TEXT NOT NULL,
    station_id    INTEGER,
    title         TEXT NOT NULL,
    severity      TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_ref    TEXT,
    symptom       JSONB,
    status        TEXT NOT NULL,
    trigger_ts    TEXT,
    trigger_seq   INTEGER,
    opened_by     TEXT NOT NULL,
    notes         TEXT,
    scenario_key  TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_plant ON incidents (plant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_run ON incidents (sim_run_id, kind, station_id);

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id    TEXT PRIMARY KEY,
    incident_id         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    status              TEXT NOT NULL,
    phase               TEXT NOT NULL,
    planner_mode        TEXT NOT NULL,
    state               JSONB NOT NULL,
    version             INTEGER NOT NULL DEFAULT 0,
    step_count          INTEGER NOT NULL DEFAULT 0,
    probe_calls         INTEGER NOT NULL DEFAULT 0,
    simulations_run     INTEGER NOT NULL DEFAULT 0,
    assessment          TEXT,
    confidence          DOUBLE PRECISION,
    stop_reason         TEXT,
    started_by          TEXT NOT NULL,
    driver_heartbeat_at TEXT,
    finished_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_investigations_incident ON investigations (incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_investigations_status ON investigations (status);

CREATE TABLE IF NOT EXISTS investigation_steps (
    step_id           TEXT PRIMARY KEY,
    investigation_id  TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    kind              TEXT NOT NULL,
    agent             TEXT,
    plan              JSONB,
    plan_source       TEXT,
    plan_valid        INTEGER,
    rejection_reason  TEXT,
    evidence_ids      JSONB,
    summary           TEXT,
    duration_ms       DOUBLE PRECISION
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_steps_seq ON investigation_steps (investigation_id, seq);

CREATE TABLE IF NOT EXISTS investigation_evidence (
    evidence_id       TEXT PRIMARY KEY,
    investigation_id  TEXT NOT NULL,
    step_id           TEXT,
    created_at        TEXT NOT NULL,
    specialist        TEXT NOT NULL,
    probe             TEXT NOT NULL,
    station_id        INTEGER,
    provenance        TEXT NOT NULL,
    summary           TEXT NOT NULL,
    observed_at       TEXT,
    data              JSONB,
    effects           JSONB,
    quality           DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_evidence_investigation ON investigation_evidence (investigation_id, created_at);

CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id   TEXT PRIMARY KEY,
    incident_id         TEXT NOT NULL,
    investigation_id    TEXT NOT NULL,
    plant_id            TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT,
    status              TEXT NOT NULL,
    assessment          TEXT NOT NULL,
    root_cause_key      TEXT,
    root_cause_title    TEXT,
    confidence          DOUBLE PRECISION,
    options             JSONB NOT NULL,
    rationale           JSONB,
    selected_option_id  TEXT,
    decided_by_role     TEXT,
    decided_by_user     TEXT,
    decided_at          TEXT,
    decision_note       TEXT,
    modification        JSONB
);
CREATE INDEX IF NOT EXISTS idx_recommendations_incident ON recommendations (incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendations_plant ON recommendations (plant_id, status);

CREATE TABLE IF NOT EXISTS autonomy_decisions (
    decision_id        TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    incident_id        TEXT NOT NULL,
    recommendation_id  TEXT,
    option_id          TEXT,
    action_type        TEXT,
    decision           TEXT NOT NULL,
    required_level     TEXT,
    granted_level      TEXT,
    evaluated_for_role TEXT,
    inputs             JSONB,
    reasons            JSONB
);
CREATE INDEX IF NOT EXISTS idx_autonomy_incident ON autonomy_decisions (incident_id, created_at);

CREATE TABLE IF NOT EXISTS enterprise_actions (
    action_id           TEXT PRIMARY KEY,
    incident_id         TEXT NOT NULL,
    recommendation_id   TEXT,
    option_id           TEXT,
    plant_id            TEXT NOT NULL,
    station_id          INTEGER,
    action_type         TEXT NOT NULL,
    parameters          JSONB,
    requested_by        TEXT NOT NULL,
    approved_by         TEXT,
    approval_timestamp  TEXT,
    risk_level          TEXT,
    autonomy_level      TEXT,
    adapter             TEXT NOT NULL,
    external_reference  TEXT,
    idempotency_key     TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    executed_at         TEXT,
    status              TEXT NOT NULL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    rollback_reference  TEXT,
    audit_metadata      JSONB,
    predicted           JSONB,
    outcome_id          TEXT,
    last_error          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_idempotency ON enterprise_actions (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_actions_incident ON enterprise_actions (incident_id, created_at);

CREATE TABLE IF NOT EXISTS incident_cases (
    case_id                TEXT PRIMARY KEY,
    created_at             TEXT NOT NULL,
    updated_at             TEXT,
    plant_id               TEXT NOT NULL,
    incident_id            TEXT NOT NULL,
    problem_signature      TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    station_id             INTEGER,
    station_role           TEXT,
    zone                   TEXT,
    sensor_maturity        TEXT,
    symptoms               JSONB,
    hypotheses_considered  JSONB,
    root_cause             TEXT,
    confidence             DOUBLE PRECISION,
    assessment             TEXT,
    actions_considered     JSONB,
    selected_action        TEXT,
    simulation_prediction  JSONB,
    human_decision         TEXT,
    decision_note          TEXT,
    actual_outcome         JSONB,
    prediction_error       DOUBLE PRECISION,
    recovery_time_s        DOUBLE PRECISION,
    recurrence             INTEGER NOT NULL DEFAULT 0,
    context_text           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_incident ON incident_cases (incident_id);
CREATE INDEX IF NOT EXISTS idx_cases_signature ON incident_cases (problem_signature, plant_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id               TEXT PRIMARY KEY,
    incident_id          TEXT,
    investigation_id     TEXT,
    parent_run_id        TEXT,
    agent_name           TEXT NOT NULL,
    model_provider       TEXT,
    model_name           TEXT,
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    latency_ms           DOUBLE PRECISION,
    prompt_tokens        INTEGER DEFAULT 0,
    completion_tokens    INTEGER DEFAULT 0,
    estimated_cost_usd   DOUBLE PRECISION,
    cost_source          TEXT,
    tools_called         JSONB,
    evidence_read        INTEGER DEFAULT 0,
    output_schema_valid  INTEGER,
    verifier_status      TEXT,
    policy_status        TEXT,
    fallback_used        INTEGER DEFAULT 0,
    error                TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_investigation ON agent_runs (investigation_id, started_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_time ON agent_runs (started_at DESC);
