-- 020_simulation_experiments.sql
-- Dynamic, isolated simulation experiments (ADR-064). One experiment = a paired baseline
-- plus up to N interventions, each run on the SAME seed list against an isolated copy of
-- the physical twin (offline/sim_experiment.py). Results persist here so they survive an
-- API restart; the worker writes them as each seed finishes.
--
-- Flags are INTEGER and timestamps TEXT on purpose: the SQLite test backend shares these
-- statements' shape, and a BOOLEAN column fed an integer crashed a worker once (ADR-063).

CREATE TABLE IF NOT EXISTS simulation_experiments (
    experiment_id      TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    updated_at         TEXT,
    plant_id           TEXT NOT NULL,
    station_id         INTEGER NOT NULL,
    objective          TEXT NOT NULL,
    origin_run_id      TEXT,
    origin_proposal_id TEXT,
    requested_by       TEXT NOT NULL,
    status             TEXT NOT NULL,
    profile            TEXT,
    horizon_s          DOUBLE PRECISION NOT NULL,
    seeds              JSONB NOT NULL,
    incident           JSONB,
    interventions      JSONB NOT NULL,
    cancel_requested   INTEGER DEFAULT 0,
    pid                INTEGER,
    started_at         TEXT,
    finished_at        TEXT,
    heartbeat_at       TEXT,
    runtime_s          DOUBLE PRECISION,
    error              TEXT,
    simulator_version  TEXT,
    summary            JSONB
);
CREATE INDEX IF NOT EXISTS idx_sim_experiments_plant
    ON simulation_experiments (plant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sim_experiments_origin
    ON simulation_experiments (origin_run_id);

CREATE TABLE IF NOT EXISTS simulation_scenarios (
    scenario_id       TEXT PRIMARY KEY,
    experiment_id     TEXT NOT NULL,
    ordinal           INTEGER NOT NULL,
    label             TEXT NOT NULL,
    intervention_type TEXT,
    params            JSONB,
    is_baseline       INTEGER DEFAULT 0,
    supported         INTEGER DEFAULT 1,
    reason            TEXT,
    simulated_as      TEXT,
    summary           JSONB
);
CREATE INDEX IF NOT EXISTS idx_sim_scenarios_experiment
    ON simulation_scenarios (experiment_id, ordinal);

CREATE TABLE IF NOT EXISTS simulation_scenario_results (
    id                 BIGSERIAL PRIMARY KEY,
    created_at         TEXT NOT NULL,
    experiment_id      TEXT NOT NULL,
    scenario_id        TEXT NOT NULL,
    seed               INTEGER NOT NULL,
    status             TEXT NOT NULL,
    throughput_uph     DOUBLE PRECISION,
    wip_mean           DOUBLE PRECISION,
    blocked_pct        DOUBLE PRECISION,
    starved_pct        DOUBLE PRECISION,
    down_pct           DOUBLE PRECISION,
    downtime_s         DOUBLE PRECISION,
    expected_defects   DOUBLE PRECISION,
    alerts             INTEGER,
    bottleneck_station INTEGER,
    runtime_s          DOUBLE PRECISION,
    error              TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_results_seed
    ON simulation_scenario_results (scenario_id, seed);
