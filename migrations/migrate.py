"""Apply migrations/*.sql to Postgres in order. Idempotent.

    python -m migrations.migrate

Runs inside the `migrate` service in docker-compose.prod.yml, which builds from
serve/Dockerfile -- so this module may only import psycopg and stdlib (the same
constraint as serve/, see CLAUDE.md SS2 / ADR-033).

Tracks applied files in a `schema_migrations` table so re-running the container (a
restart, a redeploy) never re-applies a file. Each migration file's own DDL uses
`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, so even a partial re-run
of an interrupted migration is safe.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent


def _connect(database_url: str, retries: int = 10, delay_s: float = 2.0) -> "psycopg.Connection":
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            return psycopg.connect(database_url, autocommit=False)
        except psycopg.OperationalError as e:
            last_err = e
            print(f"[migrate] postgres not ready yet (attempt {attempt + 1}/{retries}): {e}")
            time.sleep(delay_s)
    raise RuntimeError(f"could not connect to postgres after {retries} attempts") from last_err


def _ensure_tracking_table(con: "psycopg.Connection") -> None:
    with con.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   filename TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ DEFAULT NOW()
               )"""
        )
    con.commit()


def _applied(con: "psycopg.Connection") -> set[str]:
    with con.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def run(database_url: str | None = None) -> list[str]:
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set -- migrate.py only targets Postgres")

    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError(f"no migration files found under {MIGRATIONS_DIR}")

    con = _connect(database_url)
    applied_now: list[str] = []
    try:
        _ensure_tracking_table(con)
        already = _applied(con)
        for path in files:
            if path.name in already:
                print(f"[migrate] skip  {path.name} (already applied)")
                continue
            sql = path.read_text(encoding="utf-8")
            print(f"[migrate] apply {path.name}")
            with con.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            con.commit()
            applied_now.append(path.name)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return applied_now


def main() -> int:
    applied = run()
    if applied:
        print(f"[migrate] applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("[migrate] schema already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
