"""DB helpers for serve/. sqlite3 + psycopg only (ADR-033).

ADR-011: the precomputed artifact database is opened READ-ONLY via a file: URI when
running on SQLite. An accidental write then raises immediately instead of silently
mutating a committed artifact and breaking the reproducibility guarantee. Supervisor
feedback goes to a SEPARATE writable database outside the image, because the container
filesystem is ephemeral.

Finals upgrade (ADR-033): when `DATABASE_URL` is set, both the artifact tables AND the new
operational tables (audit_log, model_registry, drift_metrics, sensor_health) live in the
same Postgres database -- a real database tolerates runtime writes into tables the
artifact build never touches, so there is no read-only file to protect. Without
`DATABASE_URL` (local dev with no Postgres running), the artifact file stays read-only
exactly as before, and a SEPARATE writable SQLite file -- the same pattern already used
for feedback -- backs the new operational tables.

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


def open_feedback_db() -> sqlite3.Connection:
    """Writable, ephemeral, and deliberately NOT the artifact database."""
    path = Path(os.environ.get("PULSETWIN_FEEDBACK_DB", "")) or None
    if path is None or str(path) == ".":
        path = Path(tempfile.gettempdir()) / "pulsetwin_feedback.db"
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS feedback (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               plant_id TEXT, unit_id TEXT, verdict TEXT, created_at TEXT)"""
    )
    con.commit()
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
    lead_time_min REAL);
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
    station_id INTEGER, carried_values TEXT, capability_profile TEXT);
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
    phase TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_calls_run
    ON triage_tool_calls (run_id, call_id);
"""

# Columns added to tables that already exist on disk. See _ensure_columns.
_OPS_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "triage_runs": {
        "thread_id": "TEXT",
        "turn_index": "INTEGER DEFAULT 0",
        "intent": "TEXT DEFAULT 'ask'",
        "station_id": "INTEGER",
        "carried_values": "TEXT",
        "capability_profile": "TEXT",
    },
    "triage_tool_calls": {"phase": "TEXT"},
}

# Indexes over columns that _ensure_columns may have just added. They CANNOT live in
# _OPS_SCHEMA_SQLITE: that script runs first, and on a database created before those
# columns existed the CREATE INDEX fails with `no such column` and takes the whole
# executescript with it -- which is exactly what happened the first time this ran.
_OPS_POST_MIGRATION_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_triage_runs_thread_turn"
    " ON triage_runs (thread_id, turn_index)",
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


def open_ops_db() -> sqlite3.Connection:
    """Writable store for the new operational tables when there is no Postgres.

    Mirrors migrations/002-004 in SQLite dialect (BIGSERIAL -> AUTOINCREMENT, JSONB/
    TIMESTAMPTZ -> TEXT, BOOLEAN -> INTEGER). Kept separate from the read-only artifact
    file for the same reason feedback is (ADR-011): the container filesystem is
    ephemeral and the artifact must never be mutated.
    """
    path = Path(os.environ.get("PULSETWIN_OPS_DB", "")) or None
    if path is None or str(path) == ".":
        path = Path(tempfile.gettempdir()) / "pulsetwin_ops.db"
    con = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    con.row_factory = sqlite3.Row
    # WAL + a real busy timeout, because this file is genuinely MULTI-PROCESS: the
    # backend holds a long-lived connection while `mlops.retrainer` and `genai.triage`
    # are spawned as detached subprocesses (ADR-036) and write to the same tables.
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
    _ensure_columns(con)
    con.commit()
    return con


def open_database(artifact_db_path: str | Path) -> tuple[Any, Any, str]:
    """Returns (main_con, ops_con, backend_name).

    main_con serves the precomputed artifact tables (units, genealogy, station_config,
    metrics, ...). ops_con serves tables written at runtime (audit_log, model_registry,
    drift_metrics, sensor_health). They are the SAME handle on Postgres and two DIFFERENT
    connections on the SQLite fallback -- see the module docstring.
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        handle = _pg_module().open_handle(database_url)
        return handle, handle, "postgresql"
    print("[pulsetwin] DATABASE_URL not set -- falling back to SQLite for local dev")
    return open_readonly(artifact_db_path), open_ops_db(), "sqlite"


def open_ops_only() -> tuple[Any, str]:
    """Just the operational-tables handle (audit_log, model_registry, drift_metrics,
    sensor_health) -- for offline/mlops code that never touches the precomputed artifact
    tables. Same DATABASE_URL dispatch as open_database(), so mlops/ and serve/'s new
    routers always agree on where these tables live.
    """
    database_url = os.environ.get("DATABASE_URL")
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
