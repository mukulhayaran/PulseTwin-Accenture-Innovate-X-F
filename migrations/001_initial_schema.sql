-- 001_initial_schema.sql
-- PostgreSQL DDL for every table baked by offline/build_artifact.py.
--
-- Column types follow the finals-upgrade instruction (TIMESTAMPTZ / REAL / TEXT /
-- BOOLEAN). Application code was written against SQLite's conventions (ISO-8601 TEXT
-- timestamps, 0/1 integers for booleans) -- serve/db_postgres.py normalises rows back to
-- those shapes at the query boundary, so nothing downstream needs to know which backend
-- answered. See ADR-033.
--
-- genealogy and station_readings are LIST-partitioned by plant_id (finals brief, "Partition
-- the station_readings and genealogy tables by plant_id for multi-plant scaling"). Plant
-- IDs are not station IDs -- they already appear as literals throughout this repo
-- (configs/plant_a.yaml, the Makefile) -- so naming their partitions
-- explicitly does not violate the "no hardcoded station IDs" rule. A DEFAULT partition
-- catches any plant added later without a schema change.

-- ============================================================================== units
CREATE TABLE IF NOT EXISTS units (
    unit_id             TEXT NOT NULL,
    plant_id            TEXT NOT NULL,
    variant             TEXT,
    lot_id              TEXT,
    shift_id            TEXT,
    start_ts            TIMESTAMPTZ,
    end_ts              TIMESTAMPTZ,
    prediction_ts       TIMESTAMPTZ,
    risk_score          REAL,
    calibrated_risk     REAL,
    ci_low              REAL,
    ci_high             REAL,
    above_cost_threshold BOOLEAN,
    alert_fired         BOOLEAN,
    actual_defect       BOOLEAN,
    true_defect         BOOLEAN,
    defect_mode         TEXT,
    lead_time_min       REAL,
    split               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (unit_id, plant_id)
);
CREATE INDEX IF NOT EXISTS idx_units_alert ON units (plant_id, alert_fired, calibrated_risk);
CREATE INDEX IF NOT EXISTS idx_units_lot   ON units (plant_id, lot_id);
CREATE INDEX IF NOT EXISTS idx_units_pred  ON units (plant_id, prediction_ts);

-- ======================================================================= unit_factors
CREATE TABLE IF NOT EXISTS unit_factors (
    unit_id         TEXT NOT NULL,
    plant_id        TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    feature         TEXT,
    direction       TEXT,
    contribution    REAL,
    value           REAL,
    plain_language  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (unit_id, plant_id, rank),
    CONSTRAINT fk_unit_factors_unit FOREIGN KEY (unit_id, plant_id)
        REFERENCES units (unit_id, plant_id)
);

-- =========================================================================== genealogy
CREATE TABLE IF NOT EXISTS genealogy (
    unit_id         TEXT NOT NULL,
    plant_id        TEXT NOT NULL,
    station_id      INTEGER NOT NULL,
    entry_ts        TIMESTAMPTZ,
    exit_ts         TIMESTAMPTZ,
    operator_group  TEXT,
    station_state   TEXT,
    variant         TEXT,
    lot_id          TEXT,
    cycle_s         REAL,
    buffer_wait_s   REAL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (unit_id, plant_id, station_id)
) PARTITION BY LIST (plant_id);

CREATE TABLE IF NOT EXISTS genealogy_plant_a PARTITION OF genealogy FOR VALUES IN ('plant_a');
CREATE TABLE IF NOT EXISTS genealogy_default PARTITION OF genealogy DEFAULT;

CREATE INDEX IF NOT EXISTS idx_genealogy_station ON genealogy (plant_id, station_id);

ALTER TABLE genealogy
    ADD CONSTRAINT fk_genealogy_unit FOREIGN KEY (unit_id, plant_id)
        REFERENCES units (unit_id, plant_id);

-- =================================================================== station_readings
CREATE TABLE IF NOT EXISTS station_readings (
    unit_id     TEXT NOT NULL,
    plant_id    TEXT NOT NULL,
    station_id  INTEGER NOT NULL,
    signal      TEXT NOT NULL,
    value       REAL,
    provenance  TEXT,
    ci_low      REAL,
    ci_high     REAL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (unit_id, plant_id, station_id, signal)
) PARTITION BY LIST (plant_id);

CREATE TABLE IF NOT EXISTS station_readings_plant_a PARTITION OF station_readings
    FOR VALUES IN ('plant_a');
CREATE TABLE IF NOT EXISTS station_readings_default PARTITION OF station_readings DEFAULT;

-- ====================================================================== station_stats
CREATE TABLE IF NOT EXISTS station_stats (
    plant_id            TEXT NOT NULL,
    station_id          INTEGER NOT NULL,
    hour_start          TIMESTAMPTZ NOT NULL,
    utilisation         REAL,
    blocked_pct         REAL,
    starved_pct         REAL,
    down_pct            REAL,
    mean_cycle_s        REAL,
    throughput_uph      REAL,
    lost_throughput_uph REAL,
    provenance          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, station_id, hour_start)
);
CREATE INDEX IF NOT EXISTS idx_station_stats_hour ON station_stats (plant_id, hour_start);

-- ================================================================= bottleneck_windows
CREATE TABLE IF NOT EXISTS bottleneck_windows (
    plant_id            TEXT NOT NULL,
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ,
    bottleneck_station  INTEGER,
    confidence          REAL,
    ground_truth_station INTEGER,
    correct             BOOLEAN,
    shift_event         BOOLEAN,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, window_start)
);

-- ========================================================================= checklists
CREATE TABLE IF NOT EXISTS checklists (
    unit_id             TEXT NOT NULL,
    plant_id            TEXT NOT NULL,
    station_id          INTEGER NOT NULL,
    timestamp           TIMESTAMPTZ,
    operator_group      TEXT,
    checklist_pass      BOOLEAN,
    checklist_time_s    REAL,
    notes_code          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (unit_id, plant_id, station_id)
);

-- ====================================================================== station_config
CREATE TABLE IF NOT EXISTS station_config (
    plant_id            TEXT NOT NULL,
    station_id          INTEGER NOT NULL,
    station_name        TEXT,
    zone                TEXT,
    zone_name           TEXT,
    sensor_tier         TEXT,
    nominal_cycle_s     REAL,
    buffer_capacity     INTEGER,
    sensors             TEXT,      -- JSON-encoded list, kept as TEXT to match SQLite's
                                    -- storage exactly; see serve/db_postgres.py note.
    is_inspection       BOOLEAN,
    position            INTEGER,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, station_id)
);
CREATE INDEX IF NOT EXISTS idx_station_config_position ON station_config (plant_id, position);

-- =========================================================================== economics
CREATE TABLE IF NOT EXISTS economics (
    plant_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       REAL,
    unit        TEXT,
    source      TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, key)
);

-- ============================================================================= metrics
CREATE TABLE IF NOT EXISTS metrics (
    plant_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       REAL,
    context     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, key, context)
);

CREATE TABLE IF NOT EXISTS metrics_json (
    plant_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    payload     TEXT,       -- JSON-encoded text, kept as TEXT (not JSONB) -- see note above.
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, key)
);

-- ======================================================================== whatif_grid
CREATE TABLE IF NOT EXISTS whatif_grid (
    plant_id                TEXT NOT NULL,
    station_id              INTEGER NOT NULL,
    buffer_delta            INTEGER NOT NULL,
    cycle_delta_pct         REAL NOT NULL,
    throughput_uph          REAL,
    wip_units               REAL,
    new_bottleneck_station  INTEGER,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, station_id, buffer_delta, cycle_delta_pct)
);

CREATE TABLE IF NOT EXISTS whatif_baseline (
    plant_id            TEXT PRIMARY KEY,
    throughput_uph      REAL,
    wip_units           REAL,
    bottleneck_station  INTEGER,
    window_hours        REAL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ================================================================ instrumentation_plan
CREATE TABLE IF NOT EXISTS instrumentation_plan (
    plant_id        TEXT NOT NULL,
    station_id      INTEGER NOT NULL,
    station_name    TEXT,
    zone            TEXT,
    sensor_type     TEXT,
    cost            REAL,
    cost_source     TEXT,
    delta_pr_auc    REAL,
    annual_value    REAL,
    payback_months  REAL,
    "window"        TEXT,      -- quoted: WINDOW is a reserved word in Postgres, unlike SQLite
    recommendation  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, station_id)
);
CREATE INDEX IF NOT EXISTS idx_instrumentation_payback ON instrumentation_plan (plant_id, payback_months);

-- ===================================================================== stream_index
CREATE TABLE IF NOT EXISTS stream_index (
    plant_id    TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    byte_offset BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (plant_id, ts)
);

-- ======================================================================== plant_meta
CREATE TABLE IF NOT EXISTS plant_meta (
    plant_id                TEXT PRIMARY KEY,
    name                    TEXT,
    station_count           INTEGER,
    units_per_hour_target   REAL,
    achieved_uph            REAL,
    replay_start_ts         TIMESTAMPTZ,
    replay_end_ts           TIMESTAMPTZ,
    built_at                TIMESTAMPTZ,
    alert_budget            INTEGER,
    cost_threshold          REAL,
    budget_threshold        REAL,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);
