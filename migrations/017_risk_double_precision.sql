-- 017_risk_double_precision.sql
-- Risk scores and alert thresholds are float64 in the model and the API (ADR-063).
--
-- They were REAL (float4) here. Plant A's budget threshold 0.4060785366 was stored as
-- 0.40607855, rounded UP past the score shared by its entire alert cohort, so on the
-- Postgres stack /api/predict/defect told every alerting unit "Monitor - below the
-- alarm-budget threshold". SQLite stores REAL as float8, so the test suite never saw it.
-- Existing rows keep their float4-rounded values until the artifact is reloaded
-- (make load-data, which `docker compose up` runs).

ALTER TABLE units
    ALTER COLUMN risk_score TYPE DOUBLE PRECISION,
    ALTER COLUMN calibrated_risk TYPE DOUBLE PRECISION,
    ALTER COLUMN ci_low TYPE DOUBLE PRECISION,
    ALTER COLUMN ci_high TYPE DOUBLE PRECISION;

ALTER TABLE plant_meta
    ALTER COLUMN cost_threshold TYPE DOUBLE PRECISION,
    ALTER COLUMN budget_threshold TYPE DOUBLE PRECISION;

ALTER TABLE unit_current_state ALTER COLUMN risk TYPE DOUBLE PRECISION;
ALTER TABLE station_current_state ALTER COLUMN risk TYPE DOUBLE PRECISION;
ALTER TABLE active_alerts ALTER COLUMN risk TYPE DOUBLE PRECISION;
ALTER TABLE containment_decisions ALTER COLUMN risk_at_decision TYPE DOUBLE PRECISION;
