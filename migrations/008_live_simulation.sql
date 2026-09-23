-- 008_live_simulation.sql
-- Shared, durable state for the live-synthetic demonstration. The simulator publishes
-- the same normalized event envelope that a future MQTT/MES adapter can write.

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id              TEXT PRIMARY KEY,
    plant_id            TEXT NOT NULL,
    seed                BIGINT NOT NULL,
    scenario            TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN
                            ('running','paused','stopped','completed','failed')),
    speed               REAL NOT NULL,
    severity            REAL NOT NULL,
    affected_station    INTEGER,
    affected_lot        TEXT,
    simulation_time     TIMESTAMPTZ,
    event_count         INTEGER NOT NULL DEFAULT 0,
    alerts_count        INTEGER NOT NULL DEFAULT 0,
    units_in_progress   INTEGER NOT NULL DEFAULT 0,
    max_events          INTEGER NOT NULL DEFAULT 5000,
    source              TEXT NOT NULL DEFAULT 'live_synthetic',
    producer            TEXT NOT NULL DEFAULT 'backend',
    started_at          TIMESTAMPTZ NOT NULL,
    ended_at            TIMESTAMPTZ,
    error               TEXT
);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_plant
    ON simulation_runs (plant_id, started_at DESC);

CREATE TABLE IF NOT EXISTS simulation_events (
    run_id          TEXT NOT NULL REFERENCES simulation_runs(run_id),
    seq             INTEGER NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    event_type      TEXT NOT NULL,
    unit_id         TEXT,
    station_id      INTEGER,
    payload         JSONB NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_simulation_events_time
    ON simulation_events (run_id, ts, seq);

CREATE TABLE IF NOT EXISTS unit_current_state (
    run_id          TEXT NOT NULL REFERENCES simulation_runs(run_id),
    plant_id        TEXT NOT NULL,
    unit_id         TEXT NOT NULL,
    current_station INTEGER,
    status          TEXT NOT NULL,
    risk            REAL,
    variant         TEXT,
    lot_id          TEXT,
    updated_at      TIMESTAMPTZ NOT NULL,
    inspected       INTEGER NOT NULL DEFAULT 0,
    actual_defect   INTEGER,
    PRIMARY KEY (run_id, unit_id)
);

CREATE TABLE IF NOT EXISTS station_current_state (
    run_id          TEXT NOT NULL REFERENCES simulation_runs(run_id),
    plant_id        TEXT NOT NULL,
    station_id      INTEGER NOT NULL,
    state           TEXT NOT NULL,
    unit_id         TEXT,
    cycle_time_s    REAL,
    risk            REAL,
    updated_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, station_id)
);

CREATE TABLE IF NOT EXISTS active_alerts (
    alert_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES simulation_runs(run_id),
    plant_id        TEXT NOT NULL,
    unit_id         TEXT NOT NULL,
    station_id      INTEGER,
    risk            REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'OPEN',
    opened_at       TIMESTAMPTZ NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    outcome         INTEGER,
    outcome_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_active_alerts_run
    ON active_alerts (run_id, opened_at DESC);

ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS alert_id TEXT;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS owner TEXT;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ACTIONED';
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS due_at TEXT;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS actual_outcome INTEGER;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS outcome_at TEXT;
ALTER TABLE containment_decisions ADD COLUMN IF NOT EXISTS validation_result TEXT;
CREATE INDEX IF NOT EXISTS idx_containment_decisions_run
    ON containment_decisions (run_id, target_unit_id, created_at DESC);
