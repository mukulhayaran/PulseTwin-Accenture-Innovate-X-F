"""Champion/challenger CRUD against the `model_registry` table (migrations/003).

    from mlops.model_registry import register, promote, rollback, get_champion, history

Uses serve.db's dual Postgres/SQLite dispatch (serve.db.open_ops_only()) so this module,
mlops/retrainer.py, and serve/routers/model_health.py always agree on where the table
lives without any of them caring which backend is active.

serve/routers/admin.py implements its OWN promote()/rollback() in plain SQL rather than
importing this module: the import wall (CLAUDE.md SS2) allowlists "serve" as the only
first-party package inside serve/, so `mlops` cannot cross that boundary. The two copies
must stay in sync -- see the cross-reference comment in admin.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from serve import db


def _con():
    con, _backend = db.open_ops_only()
    return con


def _utc_now() -> str:
    """UTC, not `time.strftime` local naive -- see serve/routers/admin.py's _utc_now for
    the `age_days: -1` this mismatch produced. Both modules write the same columns and
    must agree on the clock."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def register(model_id: str, model_type: str, version: int, plant_id: str, trained_at: str,
             training_data_range: str, metrics: dict, artifact_path: str,
             status: str = "challenger", notes: str = "") -> None:
    con = _con()
    db.execute(
        con,
        """INSERT INTO model_registry
               (model_id, model_type, version, status, plant_id, trained_at,
                training_data_range, metrics, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (model_id, model_type, version, status, plant_id, trained_at,
         training_data_range, json.dumps(metrics, default=float), artifact_path, notes),
    )


def _decode(row: dict | None) -> dict | None:
    if row and row.get("metrics") is not None and isinstance(row["metrics"], str):
        row["metrics"] = json.loads(row["metrics"])
    return row


def get(model_id: str) -> dict | None:
    return _decode(db.query_one(_con(), "SELECT * FROM model_registry WHERE model_id = ?",
                                 (model_id,)))


def get_champion(plant_id: str, model_type: str = "defect_risk") -> dict | None:
    return _decode(db.query_one(
        _con(),
        """SELECT * FROM model_registry WHERE plant_id=? AND model_type=? AND status='champion'
           ORDER BY trained_at DESC LIMIT 1""",
        (plant_id, model_type),
    ))


def get_challenger(plant_id: str, model_type: str = "defect_risk") -> dict | None:
    return _decode(db.query_one(
        _con(),
        """SELECT * FROM model_registry WHERE plant_id=? AND model_type=? AND status='challenger'
           ORDER BY trained_at DESC LIMIT 1""",
        (plant_id, model_type),
    ))


def history(plant_id: str, model_type: str = "defect_risk", limit: int = 20) -> list[dict]:
    rows = db.query_all(
        _con(),
        """SELECT * FROM model_registry WHERE plant_id=? AND model_type=?
           ORDER BY trained_at DESC LIMIT ?""",
        (plant_id, model_type, limit),
    )
    return [_decode(r) for r in rows]


def promote(model_id: str, promoted_by: str = "system") -> dict:
    """Promote a challenger to champion and retire the previous one. Mirrors
    serve/routers/admin.py's promote_model() -- keep both in sync."""
    con = _con()
    challenger = db.query_one(con, "SELECT * FROM model_registry WHERE model_id=?", (model_id,))
    if not challenger:
        raise ValueError(f"no such model: {model_id}")
    if challenger["status"] != "challenger":
        raise ValueError(f"model {model_id} is '{challenger['status']}', not a challenger")

    now = _utc_now()
    previous = db.query_one(
        con,
        "SELECT model_id FROM model_registry WHERE plant_id=? AND model_type=? AND status='champion'",
        (challenger["plant_id"], challenger["model_type"]),
    )
    if previous:
        db.execute(con, "UPDATE model_registry SET status='retired', retired_at=? WHERE model_id=?",
                   (now, previous["model_id"]))
    db.execute(
        con,
        "UPDATE model_registry SET status='champion', promoted_at=?, promoted_by=? WHERE model_id=?",
        (now, promoted_by, model_id),
    )
    return {"promoted": model_id, "previous_champion": previous["model_id"] if previous else None}


def rollback(plant_id: str, model_type: str = "defect_risk") -> dict:
    """Revert to the most recently retired model -- one-click undo for a bad promotion."""
    con = _con()
    current = db.query_one(
        con,
        "SELECT model_id FROM model_registry WHERE plant_id=? AND model_type=? AND status='champion'",
        (plant_id, model_type),
    )
    previous = db.query_one(
        con,
        """SELECT * FROM model_registry WHERE plant_id=? AND model_type=? AND status='retired'
           ORDER BY retired_at DESC LIMIT 1""",
        (plant_id, model_type),
    )
    if not previous:
        raise ValueError(f"no retired model to roll back to for {plant_id}/{model_type}")
    now = _utc_now()
    if current:
        db.execute(con, "UPDATE model_registry SET status='retired', retired_at=? WHERE model_id=?",
                   (now, current["model_id"]))
    # promoted_by too, not just promoted_at: serve/routers/admin.py's rollback_model() sets
    # both, and this copy setting only one is exactly the drift the module docstring warns
    # about -- a model rolled back through the API recorded an approver, the same rollback
    # through this module recorded none.
    db.execute(con, "UPDATE model_registry SET status='champion', promoted_at=?, promoted_by=? "
                    "WHERE model_id=?", (now, "system_rollback", previous["model_id"]))
    return {"rolled_back_to": previous["model_id"],
            "replaced": current["model_id"] if current else None}


def mark_failed(model_id: str, notes: str) -> None:
    """Record a failed training run.

    INSERT-then-UPDATE, not UPDATE alone. mlops/retrainer.py calls this from its `except`
    block, and the most common failure (training itself raising) happens BEFORE register()
    has inserted the row -- so a bare UPDATE matched zero rows and the failure left no
    trace at all in the registry. `status='failed'` is one of the four states
    migrations/003 defines, and it was unreachable on the path that actually produces it.
    """
    con = _con()
    if db.query_one(con, "SELECT model_id FROM model_registry WHERE model_id=?", (model_id,)):
        db.execute(con, "UPDATE model_registry SET status='failed', notes=? WHERE model_id=?",
                   (notes, model_id))
        return
    plant_id = model_id.split("_defect_")[0] if "_defect_" in model_id else "unknown"
    db.execute(
        con,
        """INSERT INTO model_registry
               (model_id, model_type, version, status, plant_id, trained_at,
                training_data_range, metrics, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (model_id, "defect_risk", 0, "failed", plant_id, _utc_now(), "", "{}", "", notes),
    )
