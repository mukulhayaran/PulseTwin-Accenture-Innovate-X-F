"""Thin sqlite3 helpers. Stdlib only.

ADR-011: the artifact database is opened READ-ONLY via a file: URI. An accidental write
then raises immediately instead of silently mutating a committed artifact and breaking
the reproducibility guarantee. Supervisor feedback goes to a SEPARATE writable database
outside the image, because the container filesystem is ephemeral.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()


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


def query_all(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def query_one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with _lock:
        row = con.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(con: sqlite3.Connection, sql: str, params: tuple = ()) -> None:
    with _lock:
        con.execute(sql, params)
        con.commit()
