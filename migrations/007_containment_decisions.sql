-- 007_containment_decisions.sql
-- Human containment choices remain distinct from model recommendations so future
-- outcome validation can compare recommendation, intervention, and actual result.

CREATE TABLE IF NOT EXISTS containment_decisions (
    id                 BIGSERIAL PRIMARY KEY,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    plant_id           TEXT NOT NULL,
    flagged_unit_id    TEXT NOT NULL,
    target_unit_id     TEXT NOT NULL,
    recommended_action TEXT NOT NULL CHECK (recommended_action IN ('HOLD','INSPECT','MONITOR')),
    selected_action    TEXT NOT NULL CHECK (selected_action IN ('HOLD','INSPECT','MONITOR')),
    message            TEXT NOT NULL CHECK (length(message) BETWEEN 4 AND 500),
    decided_by_role    TEXT NOT NULL,
    risk_at_decision   REAL NOT NULL CHECK (risk_at_decision BETWEEN 0 AND 1),
    shared_factors     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_containment_decisions_lookup
    ON containment_decisions
       (plant_id, flagged_unit_id, target_unit_id, created_at DESC);
