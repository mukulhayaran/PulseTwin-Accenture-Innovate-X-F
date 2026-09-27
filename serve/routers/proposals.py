"""Watch firings, agent proposals, and the human decision on them (ADR-051).

    GET  /api/watch/firings           what the watch saw -- READ-ONLY, and the one
                                       endpoint here that is also an agent tool
    GET  /api/proposals               each firing, the agent's VERIFIED proposed
                                       actions, and what a human decided
    POST /api/proposals/{id}/approve  a human picks one proposed action; it is applied
                                       to the running twin (never to real equipment)
    POST /api/proposals/{id}/reject   a human declines, with a note

Every firing also spawns a Monte Carlo counterfactual (`offline.counterfactual`, ADR-055):
the physical twin forked into paired futures -- doing nothing, and each catalogue action.
A proposal is not decidable until that has finished: nothing reaches a human without its
simulated impact and its do-nothing forecast beside it.

The agent's proposed actions are never copied into `action_proposals`: they are read
from the linked triage run's served (post-verification) finding, so what the agent
proposed has one source of truth and an action the verifier dropped can never be
approved. Approve/reject are NOT in genai/tools.py -- an agent proposes, only a person
decides.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from serve import db, outcomes
from serve.live_simulation import MITIGATIONS
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role
from serve.routers.triage import ROOT, WORKER_STALE_S, _jobs_dir, _worker_is_dead

router = APIRouter()

DETECTOR_PATTERN = "^(starvation|paint_env|tool_wear)$"
DECIDABLE = "PROPOSED"
AGENT_ACTIVE = ("queued", "running")
AGENT_SERVED = ("verified", "verified_with_redactions")
SIMULATING = "SIMULATING"
FORECAST_ACTIVE = ("queued", "running")
MAX_FORECAST_JOBS = 2
_FORECAST_JOBS: list[subprocess.Popen] = []


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


def _forecast_path(proposal_id: str) -> Path:
    return _jobs_dir() / f"{proposal_id}.forecast.json"


def start_counterfactual(proposal_id: str, plant_id: str, detector: str, station_id: int,
                         evidence: dict[str, Any], budget_threshold: float) -> None:
    """Spawn the detached counterfactual job -- the triage pattern: status file written
    BEFORE the spawn, stdio to a per-job log rather than DEVNULL (CLAUDE.md section 8)."""
    jobs = _jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)
    out = _forecast_path(proposal_id)
    # Each job is a CPU-bound physical-twin Monte Carlo (~160 MB). Uncapped, a scenario
    # that fires repeatedly stacked 32 of them and starved the whole host. Refuse past
    # the cap with a visible reason -- the UI already says "decide on the evidence alone".
    # ponytail: per-process cap; a shared queue if serve ever runs >1 worker.
    _FORECAST_JOBS[:] = [p for p in _FORECAST_JOBS if p.poll() is None]
    if len(_FORECAST_JOBS) >= MAX_FORECAST_JOBS:
        out.write_text(json.dumps({"version": 1, "status": "failed", "error":
                                   f"twin busy: {len(_FORECAST_JOBS)} forecasts already running"}),
                       encoding="utf-8")
        return
    out.write_text(json.dumps({"version": 1, "status": "queued"}), encoding="utf-8")
    log = open(jobs / f"{proposal_id}.forecast.log", "w", encoding="utf-8")  # noqa: SIM115
    _FORECAST_JOBS.append(subprocess.Popen(
        [sys.executable, "-m", "offline.counterfactual", "--plant", plant_id,
         "--detector", detector, "--station", str(station_id),
         "--evidence", json.dumps(evidence), "--budget-threshold", str(budget_threshold),
         "--out", str(out)],
        cwd=str(ROOT),
        env={**os.environ, "OMP_NUM_THREADS": "1", "PYTHONPATH": str(ROOT)},
        stdout=log, stderr=subprocess.STDOUT,
    ))


def _counterfactual(proposal_id: str) -> dict[str, Any] | None:
    """The job's result; None when none was started (jobs disabled, or a row older than
    ADR-055). A job silent past WORKER_STALE_S is reported stopped, never `running`."""
    path = _forecast_path(proposal_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - path.stat().st_mtime
    except (OSError, ValueError):
        return None
    if data.get("status") in FORECAST_ACTIVE and age > WORKER_STALE_S:
        return {"version": 1, "status": "failed",
                "error": f"worker_stopped: no result for {int(WORKER_STALE_S)}s"}
    return data


LIFECYCLE = ("investigating", "forming_hypotheses", "simulating", "comparing", "verifying",
             "awaiting_approval", "applied", "monitoring", "outcome_evaluated")


def lifecycle(status: str, experiments: list[dict[str, Any]], outcome: dict[str, Any] | None,
              finding: dict[str, Any] | None) -> dict[str, Any]:
    """The shared lifecycle stage, DERIVED from recorded state -- never guessed.

    A stage the recorded state cannot establish is not marked reached: hypotheses count
    only when the verified finding carries them, simulation only when an experiment or
    forecast row exists."""
    active_exp = any(e["status"] in ("queued", "running") for e in experiments)
    if outcome and outcome["status"] in outcomes.TERMINAL:
        phase = "outcome_evaluated"
    elif outcome:
        phase = "monitoring"
    elif status in ("APPLIED", "APPROVED"):
        phase = "applied"
    elif status == DECIDABLE:
        phase = "awaiting_approval"
    elif status == SIMULATING or active_exp:
        phase = "simulating"
    elif status == "INVESTIGATING":
        phase = "comparing" if experiments else "investigating"
    else:
        phase = None
    terminal = {"REJECTED": "rejected", "NO_ACTION": "no_action", "NO_AGENT": "no_agent",
                "AGENT_FAILED": "agent_failed", "EFFECTIVE": None}.get(status)
    if status == "EFFECTIVE" and not outcome:
        phase = "applied"
    if terminal and phase is None:
        # Ended without a decision: keep what was recorded, but no stage is current.
        phase_reached = "simulating" if experiments else "investigating"
    else:
        phase_reached = phase
    if terminal:
        phase = None
    reached = []
    if phase_reached:
        for stage in LIFECYCLE[: LIFECYCLE.index(phase_reached) + 1]:
            if stage == "forming_hypotheses" and not (finding or {}).get("hypotheses"):
                continue
            if stage in ("simulating", "comparing") and not experiments and phase_reached != stage:
                continue
            reached.append(stage)
    return {"phase": phase, "reached": reached, "terminal": terminal, "stages": list(LIFECYCLE)}


def _extras(ops: Any, row: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The proposal's linked experiments and its outcome window (ADR-064)."""
    experiments = db.query_all(
        ops, "SELECT experiment_id, status, created_at, summary FROM simulation_experiments "
             "WHERE origin_proposal_id=? OR (origin_run_id IS NOT NULL AND origin_run_id=?) "
             "ORDER BY created_at DESC LIMIT 5",
        (row["proposal_id"], row.get("triage_run_id") or ""))
    exps = [{"experiment_id": e["experiment_id"], "status": e["status"],
             "created_at": e["created_at"],
             "verdict": (_loads(e["summary"]) or {}).get("verdict"),
             "best_label": (_loads(e["summary"]) or {}).get("best_label")}
            for e in experiments]
    outcome = db.query_one(ops, "SELECT * FROM action_outcomes WHERE proposal_id=? "
                                "ORDER BY created_at DESC LIMIT 1", (row["proposal_id"],))
    if outcome:
        # Only the JSON columns: `_loads` maps any non-JSON text to None, which blanked
        # every status and id of a real outcome in this view (caught live, ADR-064).
        outcome = {k: (_loads(v) if k in ("thresholds", "observed", "versions") else v)
                   for k, v in outcome.items()}
    return exps, outcome


def _view(row: dict[str, Any], run: dict[str, Any] | None,
          ops: Any | None = None) -> dict[str, Any]:
    """A proposal as the UI sees it: stored decision + the agent's served finding.

    `status` is DERIVED while no human has decided: the agent's own state tells you
    whether there is anything to decide yet.
    """
    finding = _loads(run.get("finding")) if run else None
    counterfactual = _counterfactual(row["proposal_id"])
    arms = {a["action_type"]: a for a in (counterfactual or {}).get("arms") or []}
    # Each verified action carries its own simulated impact; None when the catalogue
    # action has no arm (the job has not finished, or failed).
    actions = [{**a, "simulated_impact": arms.get(a.get("action_type"))}
               for a in (finding or {}).get("proposed_actions") or []]
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
        if (status == DECIDABLE and counterfactual
                and counterfactual.get("status") in FORECAST_ACTIVE):
            status = SIMULATING
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
        "counterfactual": counterfactual,
        "do_nothing_forecast": arms.get("do_nothing"),
        "selected_action": _loads(row.get("selected_action")),
        "decided_by_role": row.get("decided_by_role"),
        "decided_at": row.get("decided_at"),
        "decision_note": row.get("decision_note"),
        "applied_at": row.get("applied_at"),
        "effective_at": row.get("effective_at"),
        **_lifecycle_fields(ops, row, status, finding),
    }


def _lifecycle_fields(ops: Any | None, row: dict[str, Any], status: str,
                      finding: dict[str, Any] | None) -> dict[str, Any]:
    if ops is None:
        return {}
    exps, outcome = _extras(ops, row)
    return {"experiments": exps, "outcome": outcome,
            "lifecycle": lifecycle(status, exps, outcome, finding)}


def _load(ops: Any, proposal_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    row = db.query_one(ops, "SELECT * FROM action_proposals WHERE proposal_id = ?",
                       (proposal_id,))
    if row is None:
        raise HTTPException(404, f"unknown proposal '{proposal_id}'")
    return row, _agent_run(ops, row.get("triage_run_id"))


def _agent_run(ops: Any, run_id: str | None) -> dict[str, Any] | None:
    """The proposal's agent run. A dead worker never records its end, so it is reported
    failed here as in /api/triage/runs -- else the proposal read INVESTIGATING forever."""
    if not run_id:
        return None
    run = db.query_one(ops, "SELECT run_id, created_at, status, mode, finding "
                            "FROM triage_runs WHERE run_id = ?", (run_id,))
    if run and run["status"] in AGENT_ACTIVE and _worker_is_dead(run_id, run["created_at"]):
        run = {**run, "status": "failed"}
    return run


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
        out.append(_view(row, _agent_run(ops, row.get("triage_run_id")), ops))
    by_status: dict[str, int] = {}
    for p in out:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    outcome_status: dict[str, int] = {}
    for p in out:
        if p.get("outcome"):
            k = p["outcome"]["status"]
            outcome_status[k] = outcome_status.get(k, 0) + 1
    # Counts over the RETURNED proposals, so a handover can cite them rather than count.
    return {"plant_id": plant_id, "proposals": out, "count": len(out),
            "by_status": {k: by_status.get(k, 0) for k in (
                "PROPOSED", "APPLIED", "APPROVED", "EFFECTIVE", "REJECTED",
                "INVESTIGATING", "SIMULATING", "NO_ACTION", "AGENT_FAILED", "NO_AGENT")},
            "outcomes_by_status": {k: outcome_status.get(k, 0) for k in (
                "observing", "successful", "partially_successful", "no_material_effect",
                "harmful", "inconclusive", "cancelled")}}


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
    # ADR-064: structured memory of the decision, then -- only if the action really
    # reached the simulated twin -- a deterministic monitoring window on its effect.
    model = (request.app.state.model_info or {}).get(row["plant_id"]) or {}
    outcomes.record_decision(ops, row, decision="approved", role=role, note=req.note,
                             action=action, model_version=model.get("model_id"))
    outcome_id = (request.app.state.live_simulation.open_outcome(
        row["sim_run_id"], row, action["action_type"]) if applied else None)
    if outcome_id is None:
        outcomes._update_record(ops, proposal_id, success_status="not_applied",
                                confidence="low")
    return {"proposal_id": proposal_id, "status": status, "applied_to_twin": applied,
            "action": action, "outcome_id": outcome_id}


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
    # Kept for audit and for later reasoning; `not_applied`, never a failed intervention.
    model = (request.app.state.model_info or {}).get(row["plant_id"]) or {}
    outcomes.record_decision(ops, row, decision="rejected", role=role, note=req.note,
                             action=None, model_version=model.get("model_id"))
    return {"proposal_id": proposal_id, "status": "REJECTED"}
