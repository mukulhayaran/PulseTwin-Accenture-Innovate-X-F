"""Watch firings, agent proposals, and the human decision on them (ADR-051).

    GET  /api/watch/firings           what the watch saw -- READ-ONLY, and the one
                                       endpoint here that is also an agent tool
    GET  /api/proposals               each firing, the agent's VERIFIED proposed
                                       actions, and what a human decided
    POST /api/proposals/{id}/approve  a human picks one proposed action; it is applied
                                       to the running twin (never to real equipment)
    POST /api/proposals/{id}/reject   a human declines, with a note

The agent's proposed actions are never copied into `action_proposals`: they are read
from the linked triage run's served (post-verification) finding, so what the agent
proposed has one source of truth and an action the verifier dropped can never be
approved. Approve/reject are NOT in genai/tools.py -- an agent proposes, only a person
decides.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from serve import db
from serve.live_simulation import MITIGATIONS
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role

router = APIRouter()

DETECTOR_PATTERN = "^(starvation|paint_env|tool_wear)$"
DECIDABLE = "PROPOSED"
AGENT_ACTIVE = ("queued", "running")
AGENT_SERVED = ("verified", "verified_with_redactions")


def _loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/watch/firings")
def watch_firings(request: Request, plant_id: str = "plant_a",
                  detector: str | None = Query(default=None, pattern=DETECTOR_PATTERN),
                  limit: int = 10,
                  role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """The measured window evidence behind each firing, newest first."""
    limit = max(1, min(int(limit), 20))
    where, params = "plant_id = ?", [plant_id]
    if detector:
        where += " AND detector = ?"
        params.append(detector)
    rows = db.query_all(
        request.app.state.ops,
        f"""SELECT proposal_id, sim_run_id, detector, station_id, fired_at,
                   trigger_evidence
              FROM action_proposals WHERE {where}
             ORDER BY created_at DESC LIMIT ?""",
        (*params, limit),
    )
    firings = [{**{k: r[k] for k in ("proposal_id", "sim_run_id", "detector",
                                     "station_id", "fired_at")},
                "evidence": _loads(r["trigger_evidence"]) or {}} for r in rows]
    return {"plant_id": plant_id, "firings": firings, "count": len(firings)}


def _view(row: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any]:
    """A proposal as the UI sees it: stored decision + the agent's served finding.

    `status` is DERIVED while no human has decided: the agent's own state tells you
    whether there is anything to decide yet.
    """
    finding = _loads(run.get("finding")) if run else None
    actions = (finding or {}).get("proposed_actions") or []
    status = row["status"]
    if status == "OPEN":
        if not row.get("triage_run_id"):
            status = "NO_AGENT"
        elif run is None or run.get("status") in AGENT_ACTIVE:
            status = "INVESTIGATING"
        elif run.get("status") not in AGENT_SERVED:
            status = "AGENT_FAILED"
        else:
            status = DECIDABLE if actions else "NO_ACTION"
    return {
        "proposal_id": row["proposal_id"],
        "sim_run_id": row["sim_run_id"],
        "plant_id": row["plant_id"],
        "detector": row["detector"],
        "station_id": row["station_id"],
        "fired_at": row["fired_at"],
        "created_at": row["created_at"],
        "status": status,
        "trigger_evidence": _loads(row["trigger_evidence"]) or {},
        "triage_run_id": row.get("triage_run_id"),
        "agent_status": run.get("status") if run else None,
        "agent_mode": run.get("mode") if run else None,
        "finding": finding,
        "proposed_actions": actions,
        "selected_action": _loads(row.get("selected_action")),
        "decided_by_role": row.get("decided_by_role"),
        "decided_at": row.get("decided_at"),
        "decision_note": row.get("decision_note"),
        "applied_at": row.get("applied_at"),
        "effective_at": row.get("effective_at"),
    }


def _load(ops: Any, proposal_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    row = db.query_one(ops, "SELECT * FROM action_proposals WHERE proposal_id = ?",
                       (proposal_id,))
    if row is None:
        raise HTTPException(404, f"unknown proposal '{proposal_id}'")
    run = None
    if row.get("triage_run_id"):
        run = db.query_one(ops, "SELECT status, mode, finding FROM triage_runs "
                                "WHERE run_id = ?", (row["triage_run_id"],))
    return row, run


@router.get("/api/proposals")
def list_proposals(request: Request, plant_id: str = "plant_a", limit: int = 20,
                   role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    limit = max(1, min(int(limit), 50))
    rows = db.query_all(
        ops,
        "SELECT * FROM action_proposals WHERE plant_id = ? ORDER BY created_at DESC LIMIT ?",
        (plant_id, limit),
    )
    out = []
    for row in rows:
        run = None
        if row.get("triage_run_id"):
            run = db.query_one(ops, "SELECT status, mode, finding FROM triage_runs "
                                    "WHERE run_id = ?", (row["triage_run_id"],))
        out.append(_view(row, run))
    return {"plant_id": plant_id, "proposals": out, "count": len(out)}


class ApproveRequest(BaseModel):
    action_id: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=500)


class RejectRequest(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/api/proposals/{proposal_id}/approve")
def approve(proposal_id: str, req: ApproveRequest, request: Request,
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    row, run = _load(ops, proposal_id)
    view = _view(row, run)
    if view["status"] != DECIDABLE:
        raise HTTPException(409, f"proposal is {view['status']}, not {DECIDABLE}")
    action = next((a for a in view["proposed_actions"] if a.get("id") == req.action_id),
                  None)
    if action is None:
        raise HTTPException(404, f"no verified action '{req.action_id}' on this proposal")
    if action.get("action_type") not in MITIGATIONS:
        raise HTTPException(422, f"unknown action_type '{action.get('action_type')}'")

    now = _utc_now()
    applied = request.app.state.live_simulation.apply_mitigation(
        row["sim_run_id"], proposal_id, row["detector"], int(row["station_id"]),
        action["action_type"])
    status = "APPLIED" if applied else "APPROVED"
    db.execute(
        ops,
        """UPDATE action_proposals SET status=?, selected_action=?, decided_by_role=?,
               decided_at=?, decision_note=?, applied_at=? WHERE proposal_id=?""",
        (status, json.dumps(action), role, now, req.note, now if applied else None,
         proposal_id),
    )
    log_action(request, ops, role, "proposal_approved", "action_proposal", proposal_id,
               {"action_type": action["action_type"], "params": action.get("params"),
                "applied_to_twin": applied, "detector": row["detector"],
                "station_id": row["station_id"]})
    return {"proposal_id": proposal_id, "status": status, "applied_to_twin": applied,
            "action": action}


@router.post("/api/proposals/{proposal_id}/reject")
def reject(proposal_id: str, req: RejectRequest, request: Request,
           role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    row, run = _load(ops, proposal_id)
    view = _view(row, run)
    # Anything no human has decided yet may be dismissed -- including a NO_ACTION or
    # AGENT_FAILED row, which would otherwise hold its detector/station slot forever.
    if row["status"] != "OPEN":
        raise HTTPException(409, f"proposal was already {row['status']}")
    now = _utc_now()
    db.execute(
        ops,
        """UPDATE action_proposals SET status='REJECTED', decided_by_role=?, decided_at=?,
               decision_note=? WHERE proposal_id=?""",
        (role, now, req.note, proposal_id),
    )
    log_action(request, ops, role, "proposal_rejected", "action_proposal", proposal_id,
               {"was": view["status"], "detector": row["detector"],
                "station_id": row["station_id"]})
    return {"proposal_id": proposal_id, "status": "REJECTED"}
