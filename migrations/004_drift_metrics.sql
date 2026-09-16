-- 004_drift_metrics.sql
-- Drift detection (PSI, calibration, feature-level) and sensor health (Phase F2/F3).

CREATE TABLE IF NOT EXISTS drift_metrics (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    model_id        TEXT REFERENCES model_registry (model_id),
    metric_type     TEXT NOT NULL,   -- psi, calibration_drift, feature_drift
    feature_name    TEXT,            -- null for model-level metrics
    value           REAL NOT NULL,
    threshold       REAL NOT NULL,
    alert_triggered BOOLEAN DEFAULT FALSE,
    window_size     INTEGER,         -- number of units in the rolling window
    plant_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_drift_time  ON drift_metrics (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_drift_model ON drift_metrics (model_id);

CREATE TABLE IF NOT EXISTS sensor_health (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ DEFAULT NOW(),
    station_id          INTEGER NOT NULL,
    sensor_type         TEXT NOT NULL,
    status              TEXT NOT NULL,   -- healthy, degraded, dropout, offline
    last_reading_at     TIMESTAMPTZ,
    dropout_duration_s  REAL,
    plant_id            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sensor_health_latest
    ON sensor_health (plant_id, station_id, sensor_type, timestamp DESC);
