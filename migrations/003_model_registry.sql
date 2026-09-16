-- 003_model_registry.sql
-- Champion/challenger tracking for Phase F3 (mlops/model_registry.py).

CREATE TABLE IF NOT EXISTS model_registry (
    model_id            TEXT PRIMARY KEY,   -- "defect_v3_20260115"
    model_type          TEXT NOT NULL,      -- defect_risk, bottleneck_forecast, soft_sensor
    version             INTEGER NOT NULL,
    status              TEXT NOT NULL,      -- champion, challenger, retired, failed
    plant_id            TEXT NOT NULL,
    trained_at          TIMESTAMPTZ NOT NULL,
    training_data_range TEXT,               -- "2025-01-01 to 2025-01-09"
    metrics             JSONB NOT NULL,     -- {pr_auc: 0.83, precision_at_budget: 0.71, ...}
    artifact_path       TEXT NOT NULL,      -- path to the .npz file
    promoted_at         TIMESTAMPTZ,
    retired_at          TIMESTAMPTZ,
    promoted_by         TEXT,               -- user who approved promotion
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_model_registry_status ON model_registry (plant_id, model_type, status);

CREATE TABLE IF NOT EXISTS model_predictions_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    model_id        TEXT REFERENCES model_registry (model_id),
    unit_id         TEXT,
    prediction      REAL,
    actual_outcome  INTEGER,     -- filled in once the inspection result arrives
    lead_time_min   REAL
);
CREATE INDEX IF NOT EXISTS idx_pred_log_model ON model_predictions_log (model_id, timestamp DESC);
