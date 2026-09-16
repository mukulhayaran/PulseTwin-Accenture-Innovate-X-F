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
"""


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
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(_OPS_SCHEMA_SQLITE)
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
