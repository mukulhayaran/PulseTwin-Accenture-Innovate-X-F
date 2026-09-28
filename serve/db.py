"""DB helpers for serve/. sqlite3 + psycopg only (ADR-033).

ADR-011: the precomputed artifact database is opened READ-ONLY via a file: URI when
running on SQLite. An accidental write then raises immediately instead of silently
mutating a committed artifact and breaking the reproducibility guarantee.

Finals upgrade (ADR-033): when `DATABASE_URL` is set, both the artifact tables AND the new
operational tables (audit_log, model_registry, drift_metrics, sensor_health) live in the
same Postgres database -- a real database tolerates runtime writes into tables the
artifact build never touches, so there is no read-only file to protect. Without
`DATABASE_URL` (local dev with no Postgres running), the artifact file stays read-only
exactly as before, and a SEPARATE writable SQLite file backs the new operational tables.

`query_all` / `query_one` / `execute` dispatch on the connection's type, so every existing
call site in serve/main.py (~30 of them, all written in sqlite3's `?` placeholder style)
works unchanged against either backend.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()

_pg = None  # lazily-imported serve.db_postgres; only touched when DATABASE_URL is set


def _pg_module():
    global _pg
    if _pg is None:
        from serve import db_postgres as _pg_mod
        _pg = _pg_mod
    return _pg


def open_readonly(path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


_OPS_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    user_role TEXT NOT NULL, user_id TEXT, action TEXT NOT NULL,
    resource_type TEXT, resource_id TEXT, payload TEXT, ip_address TEXT, session_id TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_role ON audit_log (user_role);

CREATE TABLE IF NOT EXISTS containment_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    plant_id TEXT NOT NULL,
    flagged_unit_id TEXT NOT NULL,
    target_unit_id TEXT NOT NULL,
    recommended_action TEXT NOT NULL CHECK (recommended_action IN ('HOLD','INSPECT','MONITOR')),
    selected_action TEXT NOT NULL CHECK (selected_action IN ('HOLD','INSPECT','MONITOR')),
    message TEXT NOT NULL CHECK (length(message) BETWEEN 4 AND 500),
    decided_by_role TEXT NOT NULL,
    decided_by_user TEXT,
    risk_at_decision REAL NOT NULL CHECK (risk_at_decision BETWEEN 0 AND 1),
    shared_factors TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_containment_decisions_lookup
    ON containment_decisions (plant_id, flagged_unit_id, target_unit_id, created_at DESC);

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id TEXT PRIMARY KEY, plant_id TEXT NOT NULL, seed INTEGER NOT NULL,
    scenario TEXT NOT NULL, status TEXT NOT NULL, speed REAL NOT NULL,
    severity REAL NOT NULL, affected_station INTEGER, affected_lot TEXT,
    incident_start_s REAL NOT NULL DEFAULT 0,
    simulation_time TEXT, event_count INTEGER NOT NULL DEFAULT 0,
    alerts_count INTEGER NOT NULL DEFAULT 0, units_in_progress INTEGER NOT NULL DEFAULT 0,
    max_events INTEGER NOT NULL DEFAULT 5000, source TEXT NOT NULL DEFAULT 'live_synthetic',
    producer TEXT NOT NULL DEFAULT 'backend',
    started_at TEXT NOT NULL, ended_at TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_plant
    ON simulation_runs (plant_id, started_at DESC);

CREATE TABLE IF NOT EXISTS simulation_events (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT NOT NULL,
    event_type TEXT NOT NULL, unit_id TEXT, station_id INTEGER, payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq));
CREATE INDEX IF NOT EXISTS idx_simulation_events_time
    ON simulation_events (run_id, ts, seq);

CREATE TABLE IF NOT EXISTS unit_current_state (
    run_id TEXT NOT NULL, plant_id TEXT NOT NULL, unit_id TEXT NOT NULL,
    current_station INTEGER, status TEXT NOT NULL, risk REAL, variant TEXT, lot_id TEXT,
    updated_at TEXT NOT NULL, inspected INTEGER NOT NULL DEFAULT 0, actual_defect INTEGER,
    PRIMARY KEY (run_id, unit_id));

CREATE TABLE IF NOT EXISTS station_current_state (
    run_id TEXT NOT NULL, plant_id TEXT NOT NULL, station_id INTEGER NOT NULL,
    state TEXT NOT NULL, unit_id TEXT, cycle_time_s REAL, risk REAL, updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, station_id));

CREATE TABLE IF NOT EXISTS active_alerts (
    alert_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, plant_id TEXT NOT NULL,
    unit_id TEXT NOT NULL, station_id INTEGER, risk REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN', opened_at TEXT NOT NULL,
    acknowledged_at TEXT, actioned_at TEXT, validated_at TEXT, closed_at TEXT,
    outcome INTEGER, outcome_at TEXT);
CREATE INDEX IF NOT EXISTS idx_active_alerts_run
    ON active_alerts (run_id, opened_at DESC);

CREATE TABLE IF NOT EXISTS model_registry (
    model_id TEXT PRIMARY KEY, model_type TEXT NOT NULL, version INTEGER NOT NULL,
    status TEXT NOT NULL, plant_id TEXT NOT NULL, trained_at TEXT NOT NULL,
    training_data_range TEXT, metrics TEXT NOT NULL, artifact_path TEXT NOT NULL,
    promoted_at TEXT, retired_at TEXT, promoted_by TEXT, notes TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')));

CREATE TABLE IF NOT EXISTS model_predictions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    model_id TEXT, unit_id TEXT, prediction REAL, actual_outcome INTEGER,
    lead_time_min REAL, run_id TEXT, model_version INTEGER,
    feature_set_version TEXT, prediction_ts TEXT, features_available INTEGER,
    features_missing INTEGER, data_completeness_pct REAL, source TEXT);
CREATE INDEX IF NOT EXISTS idx_pred_log_model ON model_predictions_log (model_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS drift_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    model_id TEXT, metric_type TEXT NOT NULL, feature_name TEXT, value REAL NOT NULL,
    threshold REAL NOT NULL, alert_triggered INTEGER DEFAULT 0, window_size INTEGER,
    plant_id TEXT);
CREATE INDEX IF NOT EXISTS idx_drift_time ON drift_metrics (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_drift_model ON drift_metrics (model_id);

CREATE TABLE IF NOT EXISTS sensor_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    station_id INTEGER NOT NULL, sensor_type TEXT NOT NULL, status TEXT NOT NULL,
    last_reading_at TEXT, dropout_duration_s REAL, plant_id TEXT);
CREATE INDEX IF NOT EXISTS idx_sensor_health_latest
    ON sensor_health (plant_id, station_id, sensor_type, timestamp DESC);

CREATE TABLE IF NOT EXISTS triage_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    plant_id TEXT NOT NULL, question TEXT NOT NULL,
    requested_by TEXT NOT NULL, acting_role TEXT NOT NULL,
    mode TEXT NOT NULL, status TEXT NOT NULL,
    provider TEXT, model_id TEXT,
    finding TEXT, finding_raw TEXT, verification TEXT,
    claims_total INTEGER DEFAULT 0, claims_verified INTEGER DEFAULT 0,
    iterations INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL, cost_source TEXT, latency_s REAL, error TEXT,
    thread_id TEXT, turn_index INTEGER DEFAULT 0, intent TEXT DEFAULT 'ask',
    station_id INTEGER, carried_values TEXT, capability_profile TEXT,
    orchestrated INTEGER DEFAULT 0, agents_used TEXT);
CREATE INDEX IF NOT EXISTS idx_triage_runs_plant
    ON triage_runs (plant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS triage_threads (
    thread_id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    plant_id TEXT NOT NULL, title TEXT NOT NULL, opened_by TEXT NOT NULL,
    turn_count INTEGER DEFAULT 0, last_run_at TEXT);
CREATE INDEX IF NOT EXISTS idx_triage_threads_plant
    ON triage_threads (plant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS triage_tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, call_id TEXT NOT NULL, seq INTEGER NOT NULL,
    tool_name TEXT NOT NULL, arguments TEXT, result TEXT, duration_ms REAL,
    phase TEXT, agent TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_calls_run
    ON triage_tool_calls (run_id, call_id);

-- ADR-051: one row per watch firing. The agent's proposed actions are NOT copied here --
-- they are read from the linked triage run's VERIFIED finding, so there is one source of
-- truth for what the agent proposed. This row holds only what a human decided.
CREATE TABLE IF NOT EXISTS action_proposals (
    proposal_id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sim_run_id TEXT NOT NULL, plant_id TEXT NOT NULL,
    detector TEXT NOT NULL, station_id INTEGER NOT NULL,
    fired_at TEXT NOT NULL, trigger_evidence TEXT NOT NULL,
    triage_run_id TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    selected_action TEXT, decided_by_role TEXT, decided_at TEXT, decision_note TEXT,
    applied_at TEXT, effective_at TEXT);
CREATE INDEX IF NOT EXISTS idx_action_proposals_plant
    ON action_proposals (plant_id, created_at DESC);

-- ADR-064: migrations/020_simulation_experiments.sql + 021_outcome_learning.sql,
-- converted (JSONB -> TEXT, DOUBLE PRECISION -> REAL, BIGSERIAL -> AUTOINCREMENT).
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
    horizon_s          REAL NOT NULL,
    seeds              TEXT NOT NULL,
    incident           TEXT,
    interventions      TEXT NOT NULL,
    cancel_requested   INTEGER DEFAULT 0,
    pid                INTEGER,
    started_at         TEXT,
    finished_at        TEXT,
    heartbeat_at       TEXT,
    runtime_s          REAL,
    error              TEXT,
    simulator_version  TEXT,
    summary            TEXT
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
    params            TEXT,
    is_baseline       INTEGER DEFAULT 0,
    supported         INTEGER DEFAULT 1,
    reason            TEXT,
    simulated_as      TEXT,
    summary           TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_scenarios_experiment
    ON simulation_scenarios (experiment_id, ordinal);

CREATE TABLE IF NOT EXISTS simulation_scenario_results (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         TEXT NOT NULL,
    experiment_id      TEXT NOT NULL,
    scenario_id        TEXT NOT NULL,
    seed               INTEGER NOT NULL,
    status             TEXT NOT NULL,
    throughput_uph     REAL,
    wip_mean           REAL,
    blocked_pct        REAL,
    starved_pct        REAL,
    down_pct           REAL,
    downtime_s         REAL,
    expected_defects   REAL,
    alerts             INTEGER,
    bottleneck_station INTEGER,
    runtime_s          REAL,
    error              TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sim_results_seed
    ON simulation_scenario_results (scenario_id, seed);
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
    window_s           REAL,
    window_ends_at     TEXT,
    baseline_value     REAL,
    expected_change    REAL,
    expected_ci_low    REAL,
    expected_ci_high   REAL,
    expected_source    TEXT,
    actual_value       REAL,
    actual_change      REAL,
    prediction_error   REAL,
    n_events_before    INTEGER,
    n_events_after     INTEGER,
    thresholds         TEXT,
    observed           TEXT,
    reason             TEXT,
    evaluated_at       TEXT,
    versions           TEXT
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
    trigger_ref        TEXT,
    triage_run_id      TEXT,
    proposal_id        TEXT,
    decision           TEXT NOT NULL,
    decided_by_role    TEXT,
    decision_note      TEXT,
    intervention_type  TEXT,
    action             TEXT,
    experiment_id      TEXT,
    outcome_id         TEXT,
    expected           TEXT,
    baseline           TEXT,
    actual             TEXT,
    prediction_error   REAL,
    success_status     TEXT,
    confidence         TEXT,
    context            TEXT,
    simulator_version  TEXT,
    model_version      TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_lookup
    ON incident_learning_records (plant_id, incident_type, station_id);
CREATE INDEX IF NOT EXISTS idx_learning_proposal
    ON incident_learning_records (proposal_id);
"""

# Columns added to tables that already exist on disk. See _ensure_columns.
_OPS_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "simulation_runs": {
        "producer": "TEXT NOT NULL DEFAULT 'backend'",
        "incident_start_s": "REAL NOT NULL DEFAULT 0",
    },
    "active_alerts": {
        # Set once, the moment the first alert event for a unit triggers an unattended
        # "incident" investigation (serve/live_simulation.py::_trigger_auto_investigation).
        # NULL means either no auto-investigation ran yet, or PULSETWIN_JOBS_ENABLED=0.
        "triage_run_id": "TEXT",
        "actioned_at": "TEXT", "validated_at": "TEXT", "closed_at": "TEXT",
        # ADR-063: WALL clock when the alert opened. `opened_at` is the SIMULATION clock,
        # and subtracting it from a wall-clock acknowledgement reported 241 days.
        "opened_wall_at": "TEXT",
    },
    "model_predictions_log": {
        "run_id": "TEXT", "model_version": "INTEGER", "feature_set_version": "TEXT",
        "prediction_ts": "TEXT", "features_available": "INTEGER",
        "features_missing": "INTEGER", "data_completeness_pct": "REAL", "source": "TEXT",
    },
    "containment_decisions": {
        "run_id": "TEXT",
        "alert_id": "TEXT",
        "owner": "TEXT",
        "decided_by_user": "TEXT",
        "due_station": "INTEGER",
        "status": "TEXT DEFAULT 'ACTIONED'",
        "due_at": "TEXT",
        "actual_outcome": "INTEGER",
        "outcome_at": "TEXT",
        "validation_result": "TEXT",
    },
    "triage_runs": {
        "thread_id": "TEXT",
        "turn_index": "INTEGER DEFAULT 0",
        "intent": "TEXT DEFAULT 'ask'",
        "station_id": "INTEGER",
        "carried_values": "TEXT",
        "capability_profile": "TEXT",
        "orchestrated": "INTEGER DEFAULT 0",
        "agents_used": "TEXT",
    },
    "triage_tool_calls": {"phase": "TEXT", "agent": "TEXT"},
}

# Indexes over columns that _ensure_columns may have just added. They CANNOT live in
# _OPS_SCHEMA_SQLITE: that script runs first, and on a database created before those
# columns existed the CREATE INDEX fails with `no such column` and takes the whole
# executescript with it -- which is exactly what happened the first time this ran.
_OPS_POST_MIGRATION_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_runs_thread_turn"
    " ON triage_runs (thread_id, turn_index)",
    "CREATE INDEX IF NOT EXISTS idx_containment_decisions_run"
    " ON containment_decisions (run_id, target_unit_id, created_at DESC)",
)


def _ensure_columns(con: sqlite3.Connection) -> None:
    """Additive-only SQLite migration for the long-lived ops file.

    `CREATE TABLE IF NOT EXISTS` is a NO-OP against a table that already exists, and
    SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. The ops database is not a
    rebuilt artifact -- it sits at `%TEMP%/pulsetwin_ops.db` and survives every run -- so
    without this, every column added above would be missing at RUNTIME on any machine
    that had started the server once before, and the first symptom would be an
    `OperationalError: no such column` from a detached worker whose stderr goes to a log
    file nobody is reading.

    Additive only. It cannot rename or drop; if a future change needs either, the answer
    is to delete the ops file, not to grow this into a migration framework.
    """
    for table, columns in _OPS_ADDED_COLUMNS.items():
        try:
            existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        except sqlite3.DatabaseError:
            continue
        if not existing:
            continue
        for name, decl in columns.items():
            if name not in existing:
                try:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                except sqlite3.DatabaseError:
                    # A concurrent process may have added it between the PRAGMA and here.
                    pass

    for statement in _OPS_POST_MIGRATION_INDEXES:
        try:
            con.execute(statement)
        except sqlite3.DatabaseError:
            # An ops file carrying rows from before threads existed has many runs with a
            # NULL thread_id. SQLite treats NULLs as distinct in a UNIQUE index, so this
            # normally succeeds; if a genuine duplicate exists the index is skipped and
            # the application-level turn allocation in serve/routers/triage.py still
            # holds. Never fatal at startup.
            pass


def _sqlite_from_migration(name: str) -> str:
    """ADR-067: the SQLite dialect of a Postgres migration, derived rather than
    hand-copied (the 020/021 copies above are the drift this avoids). Only the two type
    names the migration uses differ; every statement is `IF NOT EXISTS`."""
    sql = (Path(__file__).resolve().parents[1] / "migrations" / name).read_text(encoding="utf-8")
    return sql.replace("JSONB", "TEXT").replace("DOUBLE PRECISION", "REAL")


def open_ops_db() -> sqlite3.Connection:
    """Writable store for the new operational tables when there is no Postgres.

    Mirrors migrations/002-004 in SQLite dialect (BIGSERIAL -> AUTOINCREMENT, JSONB/
    TIMESTAMPTZ -> TEXT, BOOLEAN -> INTEGER). Kept separate from the read-only artifact
    file (ADR-011): the container filesystem is ephemeral and the artifact must never be
    mutated.
    """
    path = Path(os.environ.get("PULSETWIN_OPS_DB", "")) or None
    if path is None or str(path) == ".":
        path = Path(tempfile.gettempdir()) / "pulsetwin_ops.db"
    con = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    con.row_factory = sqlite3.Row
    # WAL + a real busy timeout, because this file is genuinely MULTI-PROCESS: the
    # backend holds a long-lived connection while `genai.triage` is spawned as a detached
    # subprocess (ADR-036) and writes to the same tables.
    #
    # Under the default DELETE journal a writer is blocked by any open reader, so the
    # child would die on `sqlite3.OperationalError: database is locked` while the row it
    # was meant to advance sat at 'queued' forever. Found by actually spawning the job
    # from a running server -- the standalone process never contends with anything, so
    # every direct `python -m ...` run had passed.
    #
    # WAL lets readers and one writer proceed concurrently; busy_timeout makes a
    # contended writer WAIT rather than fail instantly. synchronous=NORMAL is the
    # documented-safe pairing with WAL.
    for pragma in ("journal_mode=WAL", "busy_timeout=15000", "synchronous=NORMAL"):
        try:
            con.execute(f"PRAGMA {pragma}")
        except sqlite3.DatabaseError:
            # A filesystem that cannot do WAL (some network mounts) still works in the
            # default journal mode -- slower under contention, not broken.
            pass
    con.executescript(_OPS_SCHEMA_SQLITE)
    con.executescript(_sqlite_from_migration("022_incident_commander.sql"))
    _ensure_columns(con)
    con.commit()
    return con


def _require_postgres_url() -> str | None:
    """DATABASE_URL, or None when running on SQLite is allowed.

    ADR-052: every DEPLOYMENT sets PULSETWIN_REQUIRE_POSTGRES=1, so a missing DATABASE_URL
    is a startup failure there instead of a silent fallback to the read-only SQLite build
    artifact -- the fallback that left a deployed site reporting `database: sqlite`.
    SQLite itself stays: it is the artifact offline/load_to_postgres.py loads, and the
    backend the test suite runs on.
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url and os.environ.get("PULSETWIN_REQUIRE_POSTGRES") == "1":
        raise RuntimeError(
            "PULSETWIN_REQUIRE_POSTGRES=1 but DATABASE_URL is not set. This deployment "
            "must run on Postgres; set DATABASE_URL (for Neon, the DIRECT connection "
            "string with ?sslmode=require) instead of falling back to SQLite.")
    return database_url or None


def open_database(artifact_db_path: str | Path) -> tuple[Any, Any, str]:
    """Returns (main_con, ops_con, backend_name).

    main_con serves the precomputed artifact tables (units, genealogy, station_config,
    metrics, ...). ops_con serves tables written at runtime (audit_log, model_registry,
    drift_metrics, sensor_health). They are the SAME handle on Postgres and two DIFFERENT
    connections on the SQLite fallback -- see the module docstring.
    """
    database_url = _require_postgres_url()
    if database_url:
        handle = _pg_module().open_handle(database_url)
        return handle, handle, "postgresql"
    print("[pulsetwin] DATABASE_URL not set -- falling back to SQLite for local dev")
    return open_readonly(artifact_db_path), open_ops_db(), "sqlite"


def open_ops_only() -> tuple[Any, str]:
    """Just the operational-tables handle (audit_log, model_registry, drift_metrics,
    sensor_health) -- for offline code (mlops/drift_detector.py, genai/) that never touches
    the precomputed artifact tables. Same DATABASE_URL dispatch as open_database(), so
    those and serve/'s routers always agree on where these tables live.
    """
    database_url = _require_postgres_url()
    if database_url:
        handle = _pg_module().open_handle(database_url)
        return handle, "postgresql"
    return open_ops_db(), "sqlite"


def backend_name(con: Any) -> str:
    return "sqlite" if isinstance(con, sqlite3.Connection) else "postgresql"


def query_all(con: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    if isinstance(con, sqlite3.Connection):
        with _lock:
            rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    return _pg_module().query_all(con, sql, params)


def query_one(con: Any, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    if isinstance(con, sqlite3.Connection):
        with _lock:
            row = con.execute(sql, params).fetchone()
        return dict(row) if row else None
    return _pg_module().query_one(con, sql, params)


def execute(con: Any, sql: str, params: tuple = ()) -> None:
    if isinstance(con, sqlite3.Connection):
        with _lock:
            con.execute(sql, params)
            con.commit()
        return
    _pg_module().execute(con, sql, params)


def execute_batch(con: Any, statements: list[tuple[str, tuple]]) -> None:
    """Several writes, ONE transaction and one commit. The live-simulation producer wrote
    each event as four separately-committed statements; on the Postgres stack that capped
    a run at ~7.5 events/s whatever speed was requested (measured). Rolls back on error so
    a failed statement never leaves a write transaction open (CLAUDE.md section 8)."""
    if not statements:
        return
    if isinstance(con, sqlite3.Connection):
        with _lock:
            try:
                for sql, params in statements:
                    con.execute(sql, params)
                con.commit()
            except Exception:
                con.rollback()
                raise
        return
    _pg_module().execute_batch(con, statements)
