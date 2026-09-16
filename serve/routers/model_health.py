"""GET /api/model/health -- drift status, calibration, fallback shadow performance,
sensor coverage. Answers "how do you know when the model is wrong?" (finals brief SS1) by
reading exactly what mlops/drift_detector.py and mlops/fallback.py already computed and
stored; this router does no computation of its own (CLAUDE.md SS2: precompute heavy, serve
thin -- mlops/ is not even on serve/'s import allowlist, see ADR-033).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from serve import db, metrics
from serve.middleware.auth import require_role

router = APIRouter()


def _metric(con, plant_id: str, key: str) -> float | None:
    r = db.query_one(con, "SELECT value FROM metrics WHERE plant_id=? AND key=?", (plant_id, key))
    return r["value"] if r else None


def _latest_run(ops_con, plant_id: str, metric_type: str) -> list[dict]:
    rows = db.query_all(
        ops_con,
        """SELECT * FROM drift_metrics WHERE plant_id=? AND metric_type=?
           ORDER BY timestamp DESC LIMIT 100""",
        (plant_id, metric_type),
    )
    if not rows:
        return []
    newest = rows[0]["timestamp"]
    return [r for r in rows if r["timestamp"] == newest]


@router.get("/api/model/health")
def model_health(request: Request, plant_id: str = "plant_a",
                  role: str = Depends(require_role("leadership"))) -> dict[str, Any]:
    ops = request.app.state.ops
    main_con = request.app.state.main_con

    champion = db.query_one(
        ops,
        """SELECT * FROM model_registry WHERE plant_id=? AND model_type='defect_risk'
           AND status='champion' ORDER BY trained_at DESC LIMIT 1""",
        (plant_id,),
    )

    psi_rows = _latest_run(ops, plant_id, "psi")
    shifted = [r for r in psi_rows if r["alert_triggered"]]
    worst = max(psi_rows, key=lambda r: r["value"], default=None)
    for r in psi_rows:
        if r.get("feature_name"):
            metrics.drift_psi.labels(plant_id=plant_id, feature=r["feature_name"]).set(r["value"])

    cal_rows = _latest_run(ops, plant_id, "calibration_drift")
    cal = cal_rows[0] if cal_rows else None

    cov_rows = _latest_run(ops, plant_id, "sensor_coverage")
    cov = cov_rows[0] if cov_rows else None
    training_full = cov["threshold"] + 0.05 if cov else None  # inverse of drift_detector's threshold calc

    overall = "healthy"
    if cal and cal["alert_triggered"]:
        overall = "degraded"
    elif psi_rows and len(shifted) > len(psi_rows) // 2:
        overall = "degraded"
    elif shifted or (cov and cov["alert_triggered"]):
        overall = "warning"

    age_days = None
    if champion and champion.get("trained_at"):
        try:
            trained = datetime.fromisoformat(champion["trained_at"])
            if trained.tzinfo is None:
                trained = trained.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - trained).days
        except ValueError:
            pass

    return {
        "current_model": {
            "id": champion["model_id"] if champion else None,
            "status": champion["status"] if champion else "none",
            "age_days": age_days,
            "training_data_range": champion.get("training_data_range") if champion else None,
        },
        "drift": {
            "overall_status": overall,
            "psi_summary": {
                "features_shifted": len(shifted),
                "features_total": len(psi_rows),
                "worst_feature": (
                    {"name": worst["feature_name"], "psi": round(worst["value"], 3),
                     "status": "alert" if worst["alert_triggered"] else "warning"}
                    if worst and worst["value"] > 0.1 else None
                ),
            },
            "calibration": {
                "max_deviation_pct": cal["value"] if cal else None,
                "status": "degraded" if (cal and cal["alert_triggered"]) else "healthy",
                "last_checked_at": cal["timestamp"] if cal else None,
                "window_size": cal["window_size"] if cal else None,
            },
        },
        "fallback": {
            "status": "active" if overall == "degraded" else "standby",
            "shadow_precision": _metric(main_con, plant_id, "fallback_shadow_precision"),
            "shadow_recall": _metric(main_con, plant_id, "fallback_shadow_recall"),
        },
        "sensor_coverage": {
            "full": cov["value"] if cov else None,
            "training_coverage": {"full": training_full},
            "drift": "degraded" if (cov and cov["alert_triggered"]) else "within_tolerance",
            "measured_at": cov["timestamp"] if cov else None,
        },
        "recommendation": (
            f"Drift detected ({overall}) -- consider triggering a retrain."
            if overall != "healthy" else
            "No action needed."
        ),
    }


@router.get("/api/model/registry")
def model_registry_history(request: Request, plant_id: str = "plant_a",
                            model_type: str = "defect_risk",
                            role: str = Depends(require_role("leadership"))) -> dict[str, Any]:
    """Full champion/challenger/retired history -- backs RetrainingConsole.tsx."""
    ops = request.app.state.ops
    rows = db.query_all(
        ops,
        """SELECT model_id, status, version, trained_at, promoted_at, retired_at,
                  promoted_by, metrics, notes
             FROM model_registry WHERE plant_id=? AND model_type=?
            ORDER BY trained_at DESC LIMIT 20""",
        (plant_id, model_type),
    )
    import json as _json
    for r in rows:
        if isinstance(r.get("metrics"), str):
            try:
                r["metrics"] = _json.loads(r["metrics"])
            except _json.JSONDecodeError:
                pass
    return {"plant_id": plant_id, "model_type": model_type, "history": rows}
