"""GET /api/system/health, GET /api/system/ingestion-lag -- infra health, sensor
dropouts, DB pool stats. Answers "what's the uptime target, what happens when the twin
goes down" (finals brief SS1).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from serve import db, metrics
from serve.middleware.auth import require_role

router = APIRouter()


def _active_dropouts(ops_con, plant_id: str) -> list[dict]:
    rows = db.query_all(
        ops_con,
        """SELECT id, station_id, sensor_type, status, last_reading_at, dropout_duration_s,
                  timestamp
             FROM sensor_health WHERE plant_id=?
            ORDER BY id DESC LIMIT 200""",
        (plant_id,),
    )
    # sensor_health is a change log (one row per status transition) -- keep only the
    # latest row per (station, sensor) and report the ones currently in dropout.
    # Ordered by `id` (strictly monotonic on insert), not `timestamp` -- the column only
    # has millisecond resolution and two transitions for the same sensor can legitimately
    # land in the same millisecond, in which case `id` is the only reliable tiebreaker.
    latest: dict[tuple, dict] = {}
    for r in rows:
        key = (r["station_id"], r["sensor_type"])
        if key not in latest:
            latest[key] = r
    return [r for r in latest.values() if r["status"] == "dropout"]


@router.get("/api/system/health")
def system_health(request: Request, plant_id: str = "plant_a",
                   role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    backend = request.app.state.backend
    started = request.app.state.started

    db_info: dict[str, Any] = {"type": backend}
    if backend == "postgresql":
        from serve import db_postgres
        ok, latency_ms = db_postgres.ping(os.environ.get("DATABASE_URL", ""))
        db_info.update({"connected": ok, "latency_ms": round(latency_ms, 1) if latency_ms else None,
                         **db_postgres.pool_stats(ops)})
    else:
        db_info.update({"connected": True, "latency_ms": None})

    dropouts = _active_dropouts(ops, plant_id)
    metrics.sensor_dropouts_active.labels(plant_id=plant_id).set(len(dropouts))
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    pred_row = db.query_one(
        ops,
        "SELECT COUNT(*) c FROM model_predictions_log WHERE model_id LIKE ? AND timestamp >= ?",
        (f"{plant_id}%", hour_ago),
    )
    last_pred = db.query_one(
        ops,
        "SELECT timestamp FROM model_predictions_log WHERE model_id LIKE ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (f"{plant_id}%",),
    )

    return {
        "status": "operational" if not dropouts else "degraded",
        "uptime_seconds": round(time.time() - started, 1),
        "database": db_info,
        "ingestion": {
            "sensor_dropouts_active": len(dropouts),
            "dropout_details": [
                {"station_id": d["station_id"], "sensor": d["sensor_type"],
                 "since": d["last_reading_at"], "duration_s": d["dropout_duration_s"]}
                for d in dropouts
            ],
        },
        "last_prediction_at": last_pred["timestamp"] if last_pred else None,
        "predictions_last_hour": pred_row["c"] if pred_row else 0,
    }


@router.get("/api/system/ingestion-lag")
def ingestion_lag(request: Request, plant_id: str = "plant_a",
                   role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """Lag = how long after a sensor's own reading a status row about it was written.
    Only meaningful once ingestion/ has actually run against this plant (the SQLite-only
    local demo runtime never does) -- an empty series is the honest answer, not an error.
    """
    ops = request.app.state.ops
    rows = db.query_all(
        ops,
        """SELECT timestamp, last_reading_at FROM sensor_health
           WHERE plant_id=? ORDER BY timestamp DESC LIMIT 200""",
        (plant_id,),
    )
    lags = []
    for r in rows:
        if not r["last_reading_at"]:
            continue
        try:
            written = datetime.fromisoformat(r["timestamp"])
            observed = datetime.fromisoformat(r["last_reading_at"])
            lags.append(max(0.0, (written - observed).total_seconds()))
        except ValueError:
            continue

    current = lags[0] if lags else None
    if current is not None:
        metrics.ingestion_lag_seconds.labels(plant_id=plant_id).set(current)
    return {
        "plant_id": plant_id,
        "current_lag_s": current,
        "mean_lag_s": round(sum(lags) / len(lags), 2) if lags else None,
        "threshold_s": 5.0,
        "status": "behind" if (current or 0.0) > 5.0 else "on_time",
        "samples": len(lags),
    }
