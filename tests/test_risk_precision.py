"""ADR-063: risk scores and alert thresholds must be float64 in Postgres.

Nothing in this suite runs Postgres (SQLite stores REAL as float8), which is how a float4
threshold shipped: plant A's 0.4060785366 was stored as 0.40607855 -- above the score its
whole alert cohort shares -- and the running stack told every alerting unit to Monitor.
This reads the migrations and checks the FINAL type of every risk/threshold column.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))
MUST_BE_FLOAT8 = {
    ("units", "risk_score"), ("units", "calibrated_risk"), ("units", "ci_low"),
    ("units", "ci_high"), ("plant_meta", "cost_threshold"), ("plant_meta", "budget_threshold"),
    ("unit_current_state", "risk"), ("station_current_state", "risk"),
    ("active_alerts", "risk"), ("containment_decisions", "risk_at_decision"),
}


def _final_types() -> dict[tuple[str, str], str]:
    types: dict[tuple[str, str], str] = {}
    for f in MIGRATIONS:
        sql = f.read_text(encoding="utf-8")
        for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);",
                                      sql, flags=re.S):
            for col, typ in re.findall(r"^\s*(\w+)\s+([A-Z][A-Z ]*?)(?:[\s,(]|$)", body,
                                       flags=re.M):
                types[(table, col)] = typ.strip()
        for table, body in re.findall(r"ALTER TABLE (\w+)\s+(.*?);", sql, flags=re.S):
            for col, typ in re.findall(r"ALTER COLUMN (\w+) TYPE ([A-Z ]+?)(?:,|$)", body):
                types[(table, col)] = typ.strip()
    return types


def test_every_risk_and_threshold_column_ends_as_double_precision():
    types = _final_types()
    bad = {k: types.get(k) for k in MUST_BE_FLOAT8 if types.get(k) != "DOUBLE PRECISION"}
    assert not bad, f"float4 risk/threshold columns: {bad}"


def test_float4_really_does_move_the_plant_a_threshold_past_its_plateau():
    thr = 0.4060785365829261                       # the committed plant A budget threshold
    assert float(np.float32(thr)) > thr            # rounded UP: every plateau unit < cut


def test_every_sqlite_runtime_column_also_has_a_postgres_migration():
    """SQLite adds these at runtime (serve/db.py::_OPS_ADDED_COLUMNS); Postgres only knows
    what a migration created. active_alerts.triage_run_id existed in one and not the other,
    and the live alert-status endpoint 500'd on the Postgres stack."""
    from serve.db import _OPS_ADDED_COLUMNS
    sql = "\n".join(m.read_text(encoding="utf-8") for m in MIGRATIONS)
    missing = []
    for table, cols in _OPS_ADDED_COLUMNS.items():
        for col in cols:
            created = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);", sql, re.S)
            in_create = bool(created and re.search(rf"^\s*{col}\s", created.group(1), re.M))
            added = re.search(rf"ALTER TABLE {table}\s+ADD COLUMN (IF NOT EXISTS )?{col}\b", sql)
            if not (in_create or added):
                missing.append(f"{table}.{col}")
    assert not missing, f"columns SQLite has and Postgres lacks: {missing}"
