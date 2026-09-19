"""Alert acknowledgement and the escalation policy (configs/alert_policies.yaml is the
human-readable source these constants implement -- see that file's header for why serve/
hardcodes them rather than parsing YAML at runtime, `yaml` being outside the import wall,
CLAUDE.md SS2).

Acknowledgement has no separate state table: the audit_log entry IS the durable record of
who acknowledged what and when (queryable via GET /api/audit/log?action=alert_acknowledged).
Adding a mutable "acknowledged" flag on `units` would mean two sources of truth for the
same fact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from serve import db
from serve.middleware.audit import log_action
from serve.middleware.auth import get_role, require_role

router = APIRouter()

# Mirrors configs/alert_policies.yaml's escalation.levels -- keep both in sync.
ESCALATION_LEVELS = [
    {"level": 1, "name": "floor_supervisor", "response_window_min": 5},
    {"level": 2, "name": "shift_manager", "response_window_min": 15},
    {"level": 3, "name": "plant_leadership", "response_window_min": None},
]
CONTAINMENT_AT_RISK_THRESHOLD = 3
CONTAINMENT_SKIP_TO_LEVEL = 2


class AcknowledgeRequest(BaseModel):
    plant_id: str = "plant_a"


@router.post("/api/alerts/{unit_id}/acknowledge")
def acknowledge_alert(unit_id: str, req: AcknowledgeRequest, request: Request,
                       role: str = Depends(get_role)) -> dict[str, Any]:
    main_con = request.app.state.main_con
    unit = db.query_one(main_con, "SELECT * FROM units WHERE plant_id=? AND unit_id=?",
                         (req.plant_id, unit_id))
    if not unit:
        raise HTTPException(404, f"unknown unit '{unit_id}'")
    if not unit["alert_fired"]:
        raise HTTPException(400, f"unit '{unit_id}' has no active alert to acknowledge")

    log_action(request, request.app.state.ops, role, "alert_acknowledged", "alert",
               unit_id, {"plant_id": req.plant_id})
    return {
        "acknowledged": True, "unit_id": unit_id, "by_role": role,
        # UTC, matching the audit_log row this same call writes (whose timestamp column
        # DEFAULTs to UTC). Local time here meant the acknowledgement the UI displayed and
        # the audit entry recording it disagreed by the host's UTC offset.
        "acknowledged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.get("/api/alerts/escalation-policy")
def escalation_policy(role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    return {
        "levels": ESCALATION_LEVELS,
        "containment_escalation": {
            "at_risk_threshold": CONTAINMENT_AT_RISK_THRESHOLD,
            "skip_to_level": CONTAINMENT_SKIP_TO_LEVEL,
        },
    }
