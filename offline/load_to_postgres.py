"""Bridge: copy every row from the precomputed artifacts/pulsetwin.db (SQLite) into
PostgreSQL. Run after `make build-artifact` and after `python -m migrations.migrate`.

    DATABASE_URL=postgresql://pulsetwin:pulsetwin_dev@localhost:5432/pulsetwin \\
        python -m offline.load_to_postgres

The artifacts still exist and are still built by offline/build_artifact.py exactly as
before -- this script only changes where they end up living. Offline code, not bound by
the import wall, but deliberately uses only sqlite3 + psycopg (no pandas): this is a row
copy with type conversion, not feature engineering.

Per-plant replace semantics, mirroring build_artifact.py's own
`DELETE FROM {tbl} WHERE plant_id = ?` idiom: re-running this script for a plant that
already has rows in Postgres replaces them rather than duplicating them.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

# table -> (insert column order, columns that are 0/1 SQLite ints standing in for
# booleans, columns that are ISO-8601 text standing in for timestamps). Converting to
# native Python bool/datetime before binding avoids relying on Postgres implicit casts.
TABLES: dict[str, dict] = {
    "units": {
        "columns": ["unit_id", "plant_id", "variant", "lot_id", "shift_id", "start_ts",
                    "end_ts", "prediction_ts", "risk_score", "calibrated_risk", "ci_low",
                    "ci_high", "above_cost_threshold", "alert_fired", "actual_defect",
                    "true_defect", "defect_mode", "lead_time_min", "split"],
        "bool_cols": {"above_cost_threshold", "alert_fired", "actual_defect", "true_defect"},
        "ts_cols": {"start_ts", "end_ts", "prediction_ts"},
    },
    "unit_factors": {
        "columns": ["unit_id", "plant_id", "rank", "feature", "direction", "contribution",
                    "value", "plain_language"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "genealogy": {
        "columns": ["unit_id", "plant_id", "station_id", "entry_ts", "exit_ts",
                    "operator_group", "station_state", "variant", "lot_id", "cycle_s",
                    "buffer_wait_s"],
        "bool_cols": set(), "ts_cols": {"entry_ts", "exit_ts"},
    },
    "station_readings": {
        "columns": ["unit_id", "plant_id", "station_id", "signal", "value", "provenance",
                    "ci_low", "ci_high"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "station_stats": {
        "columns": ["plant_id", "station_id", "hour_start", "utilisation", "blocked_pct",
                    "starved_pct", "down_pct", "mean_cycle_s", "throughput_uph",
                    "lost_throughput_uph", "provenance"],
        "bool_cols": set(), "ts_cols": {"hour_start"},
    },
    "bottleneck_windows": {
        "columns": ["plant_id", "window_start", "window_end", "bottleneck_station",
                    "confidence", "ground_truth_station", "correct", "shift_event"],
        "bool_cols": {"correct", "shift_event"}, "ts_cols": {"window_start", "window_end"},
    },
    "checklists": {
        "columns": ["unit_id", "plant_id", "station_id", "timestamp", "operator_group",
                    "checklist_pass", "checklist_time_s", "notes_code"],
        "bool_cols": {"checklist_pass"}, "ts_cols": {"timestamp"},
    },
    "station_config": {
        "columns": ["plant_id", "station_id", "station_name", "zone", "zone_name",
                    "sensor_tier", "nominal_cycle_s", "buffer_capacity", "sensors",
                    "is_inspection", "position"],
        "bool_cols": {"is_inspection"}, "ts_cols": set(),
    },
    "economics": {
        "columns": ["plant_id", "key", "value", "unit", "source"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "metrics": {
        "columns": ["plant_id", "key", "value", "context"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "metrics_json": {
        "columns": ["plant_id", "key", "payload"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "whatif_grid": {
        "columns": ["plant_id", "station_id", "buffer_delta", "cycle_delta_pct",
                    "throughput_uph", "wip_units", "new_bottleneck_station"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "whatif_baseline": {
        "columns": ["plant_id", "throughput_uph", "wip_units", "bottleneck_station",
                    "window_hours"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "instrumentation_plan": {
        "columns": ["plant_id", "station_id", "station_name", "zone", "sensor_type", "cost",
                    "cost_source", "delta_pr_auc", "annual_value", "payback_months",
                    "window", "recommendation"],
        "bool_cols": set(), "ts_cols": set(),
    },
    "stream_index": {
        "columns": ["plant_id", "ts", "byte_offset"],
        "bool_cols": set(), "ts_cols": {"ts"},
    },
    "plant_meta": {
        "columns": ["plant_id", "name", "station_count", "units_per_hour_target",
                    "achieved_uph", "replay_start_ts", "replay_end_ts", "built_at",
                    "alert_budget", "cost_threshold", "budget_threshold"],
        "bool_cols": set(), "ts_cols": {"replay_start_ts", "replay_end_ts", "built_at"},
    },
}

# DELETE must go children-before-parents and INSERT parents-before-children -- the two
# FKs (unit_factors -> units, genealogy -> units) require opposite orderings for the two
# operations, not one shared list.
DELETE_ORDER = ["unit_factors", "genealogy", "units", "station_readings", "station_stats",
                "bottleneck_windows", "checklists", "station_config", "economics", "metrics",
                "metrics_json", "whatif_grid", "whatif_baseline", "instrumentation_plan",
                "stream_index", "plant_meta"]
INSERT_ORDER = ["units", "unit_factors", "genealogy", "station_readings", "station_stats",
                "bottleneck_windows", "checklists", "station_config", "economics", "metrics",
                "metrics_json", "whatif_grid", "whatif_baseline", "instrumentation_plan",
                "stream_index", "plant_meta"]


def _convert(row: sqlite3.Row, spec: dict) -> tuple:
    out = []
    for col in spec["columns"]:
        v = row[col]
        if v is not None and col in spec["bool_cols"]:
            v = bool(int(v))
        elif v is not None and col in spec["ts_cols"]:
            v = datetime.fromisoformat(v)
        out.append(v)
    return tuple(out)


def load(sqlite_path: Path, database_url: str, plant_ids: list[str] | None = None,
         batch_size: int = 2000) -> dict[str, int]:
    sconn = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    sconn.row_factory = sqlite3.Row

    if plant_ids is None:
        plant_ids = [r["plant_id"] for r in
                     sconn.execute("SELECT DISTINCT plant_id FROM plant_meta").fetchall()]
    if not plant_ids:
        raise RuntimeError(f"no plants found in {sqlite_path} -- run `make build-artifact` first")

    counts: dict[str, int] = {}
    with psycopg.connect(database_url, autocommit=False) as pconn:
        for pid in plant_ids:
            print(f"[load_to_postgres] plant '{pid}'")
            for table in DELETE_ORDER:
                with pconn.cursor() as cur:
                    cur.execute(f"DELETE FROM {table} WHERE plant_id = %s", (pid,))

            for table in INSERT_ORDER:
                spec = TABLES[table]
                cols = spec["columns"]
                # Double-quoted defensively: "window" (instrumentation_plan) is a reserved
                # word in Postgres, though not in SQLite -- quoting on both sides is safe
                # either way and future-proofs against the next reserved-word collision.
                quoted_cols = [f'"{c}"' for c in cols]
                select_sql = f"SELECT {', '.join(quoted_cols)} FROM {table} WHERE plant_id = ?"
                params: tuple = (pid,)
                if table == "genealogy":
                    # SQLite never enforced the FK genealogy -> units, and it turns out
                    # the two tables use DIFFERENT window filters at build time --
                    # units.prediction_ts +/- a 2h margin (ADR-032) vs genealogy.entry_ts
                    # with no margin -- so a unit whose journey starts inside the replay
                    # window but predicts late can appear in genealogy without a units
                    # row. Postgres's real FK caught this; filtering here (not relaxing
                    # the constraint) is the fix, matching what the FK is for.
                    select_sql += " AND unit_id IN (SELECT unit_id FROM units WHERE plant_id = ?)"
                    params = (pid, pid)
                rows = sconn.execute(select_sql, params).fetchall()

                placeholders = ", ".join(["%s"] * len(cols))
                insert_sql = (
                    f"INSERT INTO {table} ({', '.join(quoted_cols)}) VALUES ({placeholders})"
                )
                n = 0
                with pconn.cursor() as cur:
                    batch = []
                    for r in rows:
                        batch.append(_convert(r, spec))
                        if len(batch) >= batch_size:
                            cur.executemany(insert_sql, batch)
                            n += len(batch)
                            batch = []
                    if batch:
                        cur.executemany(insert_sql, batch)
                        n += len(batch)
                counts[f"{pid}.{table}"] = n
                print(f"  {table}: {n} rows")
            pconn.commit()
    sconn.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default=None, help="defaults to $DATABASE_URL")
    ap.add_argument("--plant-id", action="append", default=None,
                     help="repeatable; defaults to every plant in the artifact")
    ap.add_argument("--sqlite-path", default=str(ARTIFACTS / "pulsetwin.db"))
    args = ap.parse_args()

    import os
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set (env var or --database-url)")

    counts = load(Path(args.sqlite_path), database_url, args.plant_id)
    total = sum(counts.values())
    print(f"[load_to_postgres] done -- {total:,} rows across {len(counts)} plant.table pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
