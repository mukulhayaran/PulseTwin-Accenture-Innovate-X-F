"""PostgreSQL backend for serve/. Stdlib + psycopg only (ADR-033: a driver, not compute).

Rows are normalised back to exactly what serve/db.py's SQLite path already returns --
ISO-8601 strings for timestamps, 0/1 integers for booleans -- so serve/main.py and every
Pydantic schema behave identically no matter which database answered the query. Without
this, TIMESTAMPTZ columns would come back as `datetime` objects and BOOLEAN columns as
`True`/`False`, silently changing API responses depending on deployment.

serve/db.py dispatches to this module when DATABASE_URL is set; callers never import this
directly.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def _normalise(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list)):
        # JSONB decodes to a Python object automatically; re-encode to TEXT so JSON
        # columns look identical to callers regardless of backend (station_config.sensors,
        # metrics_json.payload, audit_log.payload, model_registry.metrics all read as a
        # JSON string everywhere and every caller does its own json.loads/json.dumps).
        return json.dumps(value)
    return value


def _row_to_dict(columns: list[str], row: tuple) -> dict[str, Any]:
    return {col: _normalise(v) for col, v in zip(columns, row)}


def _to_pg_params(sql: str) -> str:
    """Translate SQLite-style `?` placeholders to psycopg's `%s`.

    Every query in serve/ was written against sqlite3 (ADR-011 predates Postgres). None
    of them contain a literal `?` inside a string, so a straight replace is safe -- this
    keeps serve/main.py's ~30 call sites completely unchanged across both backends.
    """
    return sql.replace("?", "%s")


class PostgresHandle:
    """Duck-types enough of sqlite3.Connection for serve/db.py's dispatch to work."""

    backend = "postgresql"

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool = get_pool(database_url)

    def close(self) -> None:
        # Pools are process-lifetime and shared; individual handles do not own them.
        pass


def get_pool(database_url: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(database_url)
        if pool is None:
            pool = ConnectionPool(
                database_url, min_size=1,
                # 20 locally; Render sets 5 -- one worker never needs more, and Neon's
                # free compute caps the whole project at ~100 connections.
                max_size=int(os.environ.get("PULSETWIN_PG_POOL_MAX", "20")),
                # prepare_threshold=None: never server-side-prepare. Harmless on a direct
                # connection, and it keeps a pooled (PgBouncer, transaction-mode) Neon
                # endpoint from failing on prepared statements that don't survive a
                # transaction boundary. Use the DIRECT endpoint anyway (ADR-052).
                kwargs={"prepare_threshold": None},
                # Neon suspends compute after ~5 min idle, killing idle connections. Check
                # each one on checkout so a dead one is replaced, not handed to a request.
                check=ConnectionPool.check_connection,
                open=True,
            )
            pool.wait(timeout=30)
            _pools[database_url] = pool
        return pool


def open_handle(database_url: str) -> PostgresHandle:
    return PostgresHandle(database_url)


def pool_stats(handle: PostgresHandle) -> dict[str, int]:
    stats = handle._pool.get_stats()
    size = stats.get("pool_size", 0)
    available = stats.get("pool_available", 0)
    return {
        "connections_active": max(0, size - available),
        "connections_max": handle._pool.max_size,
    }


def query_all(handle: PostgresHandle, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    sql = _to_pg_params(sql)
    with handle._pool.connection() as con:
        with con.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall()
    return [_row_to_dict(cols, r) for r in rows]


def query_one(handle: PostgresHandle, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    sql = _to_pg_params(sql)
    with handle._pool.connection() as con:
        with con.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description] if cur.description else []
            row = cur.fetchone()
    return _row_to_dict(cols, row) if row is not None else None


def execute(handle: PostgresHandle, sql: str, params: tuple = ()) -> None:
    sql = _to_pg_params(sql)
    with handle._pool.connection() as con:
        with con.cursor() as cur:
            cur.execute(sql, params)
        con.commit()


def ping(database_url: str, timeout_s: float = 2.0) -> tuple[bool, float | None]:
    """Used by /api/system/health. Returns (ok, latency_ms)."""
    import time

    t0 = time.time()
    try:
        with psycopg.connect(database_url, connect_timeout=timeout_s) as con:
            with con.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True, (time.time() - t0) * 1000.0
    except psycopg.OperationalError:
        return False, None
