"""Alert acknowledgement, lifecycle status, closing, and escalation policy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from serve import db
from serve.middleware.audit import log_action
from serve.middleware.auth import get_role, require_role
from serve.policy import containment_action_thresholds

router = APIRouter()

# Mirrors configs/alert_policies.yaml's escalation.levels -- keep both in sync.
ESCALATION_LEVELS = [
    {"level": 1, "name": "floor_supervisor", "response_window_min": 5},
    {"level": 2, "name": "shift_manager", "response_window_min": 15},
    {"level": 3, "name": "plant_leadership", "response_window_min": None},
]
CONTAINMENT_AT_RISK_THRESHOLD = 3
CONTAINMENT_SKIP_TO_LEVEL = 2
CONTAINMENT_HOLD_RISK, CONTAINMENT_INSPECT_RISK = containment_action_thresholds()


class AcknowledgeRequest(BaseModel):
    plant_id: str = "plant_a"
    run_id: str | None = None


class CloseAlertRequest(BaseModel):
    plant_id: str = "plant_a"
    run_id: str


@router.post("/api/alerts/{unit_id}/acknowledge")
def acknowledge_alert(unit_id: str, req: AcknowledgeRequest, request: Request,
                       role: str = Depends(get_role)) -> dict[str, Any]:
    main_con = request.app.state.main_con
    unit = db.query_one(main_con, "SELECT * FROM units WHERE plant_id=? AND unit_id=?",
                         (req.plant_id, unit_id))
    if not unit:
        raise HTTPException(404, f"unknown unit '{unit_id}'")
    live_alert = None
    if req.run_id:
        live_alert = db.query_one(
            request.app.state.ops,
            "SELECT alert_id, status FROM active_alerts WHERE run_id=? AND unit_id=?",
            (req.run_id, unit_id),
        )
    if not unit["alert_fired"] and not live_alert:
        raise HTTPException(400, f"unit '{unit_id}' has no active alert to acknowledge")

    acknowledged_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if live_alert:
        db.execute(
            request.app.state.ops,
            """UPDATE active_alerts
                  SET status=CASE WHEN status='OPEN' THEN 'ACKNOWLEDGED' ELSE status END,
                      acknowledged_at=COALESCE(acknowledged_at, ?)
                 WHERE alert_id=?""",
            (acknowledged_at, live_alert["alert_id"]),
        )
    log_action(request, request.app.state.ops, role, "alert_acknowledged", "alert",
               unit_id, {"plant_id": req.plant_id, "run_id": req.run_id})
    return {
        "acknowledged": True, "unit_id": unit_id, "by_role": role,
        # UTC, matching the audit_log row this same call writes (whose timestamp column
        # DEFAULTs to UTC). Local time here meant the acknowledgement the UI displayed and
        # the audit entry recording it disagreed by the host's UTC offset.
        "acknowledged_at": acknowledged_at,
    }


@router.get("/api/alerts/{unit_id}/status")
def alert_status(unit_id: str, request: Request, plant_id: str = "plant_a",
                 run_id: str | None = None, role: str = Depends(get_role)) -> dict[str, Any]:
    """Return durable lifecycle state so refresh/reopen never resets the operator view."""
    del role
    if run_id:
        row = db.query_one(
            request.app.state.ops,
            """SELECT alert_id,status,opened_at,acknowledged_at,actioned_at,validated_at,
                      closed_at,outcome,outcome_at,triage_run_id
                 FROM active_alerts WHERE run_id=? AND plant_id=? AND unit_id=?""",
            (run_id, plant_id, unit_id),
        )
        if not row:
            raise HTTPException(404, f"no alert for unit '{unit_id}' in this run")
        return row

    unit = db.query_one(
        request.app.state.main_con,
        "SELECT alert_fired,prediction_ts FROM units WHERE plant_id=? AND unit_id=?",
        (plant_id, unit_id),
    )
    if not unit or not unit["alert_fired"]:
        raise HTTPException(404, f"unit '{unit_id}' has no alert")
    rows = db.query_all(
        request.app.state.ops,
        """SELECT timestamp,payload FROM audit_log
             WHERE action='alert_acknowledged' AND resource_id=?
             ORDER BY timestamp DESC LIMIT 20""",
        (unit_id,),
    )
    acknowledged_at = None
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                import json
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        if isinstance(payload, dict) and payload.get("plant_id") == plant_id and not payload.get("run_id"):
            acknowledged_at = row["timestamp"]
            break
    return {
        "alert_id": f"replay:{plant_id}:{unit_id}",
        "status": "ACKNOWLEDGED" if acknowledged_at else "OPEN",
        "opened_at": unit["prediction_ts"],
        "acknowledged_at": acknowledged_at,
        "actioned_at": None, "validated_at": None, "closed_at": None,
        "outcome": None, "outcome_at": None, "triage_run_id": None,
    }


@router.post("/api/alerts/{unit_id}/close")
def close_alert(unit_id: str, req: CloseAlertRequest, request: Request,
                role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    alert = db.query_one(
        request.app.state.ops,
        "SELECT alert_id, status FROM active_alerts WHERE run_id=? AND unit_id=?",
        (req.run_id, unit_id),
    )
    if not alert:
        raise HTTPException(404, f"no alert for unit '{unit_id}' in this run")
    if alert["status"] != "VALIDATED":
        raise HTTPException(409, "an alert can be closed only after its outcome is validated")
    closed_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        request.app.state.ops,
        "UPDATE active_alerts SET status='CLOSED', closed_at=? WHERE alert_id=?",
        (closed_at, alert["alert_id"]),
    )
    log_action(request, request.app.state.ops, role, "alert_closed", "alert", unit_id,
               {"plant_id": req.plant_id, "run_id": req.run_id})
    return {"closed": True, "unit_id": unit_id, "status": "CLOSED", "closed_at": closed_at}


@router.get("/api/alerts/escalation-policy")
def escalation_policy(role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    return {
        "levels": ESCALATION_LEVELS,
        "containment_escalation": {
            "at_risk_threshold": CONTAINMENT_AT_RISK_THRESHOLD,
            "skip_to_level": CONTAINMENT_SKIP_TO_LEVEL,
        },
        "containment_actions": {
            "hold_risk": CONTAINMENT_HOLD_RISK,
            "inspect_risk": CONTAINMENT_INSPECT_RISK,
        },
    }
