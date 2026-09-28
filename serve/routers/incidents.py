"""The Incident Commander API (ADR-067).

    POST /api/incidents                                   open an incident (operator report)
    GET  /api/incidents                                   list, newest first
    GET  /api/incidents/{id}                              incident + investigation + recommendation
    POST /api/incidents/{id}/investigate                  start the commander
    POST /api/incidents/{id}/investigation/continue       one step, optionally with a proposed plan
    GET  /api/incidents/{id}/investigation                state, steps, evidence, agent runs
    GET  /api/incidents/{id}/hypotheses
    GET  /api/incidents/{id}/recommendations
    GET  /api/incidents/{id}/similar-cases
    POST /api/recommendations/{id}/approve | reject | modify | request-investigation
    POST /api/actions/{id}/execute    GET /api/actions/{id}    POST /api/actions/{id}/rollback
    GET  /api/maintenance/windows
    GET  /api/ai/operations/metrics   GET /api/ai/operations/runs
    GET  /api/business/impact
    GET  /api/sensor-maturity
    GET  /api/demo/scenarios          POST /api/demo/scenarios/{key}/start

Authorization is server-side at three layers: `require_role` on every route, the autonomy
governor re-evaluated for the CALLER's role on every approval and execution (a leadership
token outranks manager in the API but may not coordinate L3 work), and an optional
per-principal plant allowlist (`PULSETWIN_PLANT_ACL`) checked against the RESOURCE's plant,
never a plant the client names.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from serve import action_gateway as G
from serve import ai_operations, case_memory, commander, db, demo_scenarios, demo_session, governor
from serve import decisions, incident_story
from serve import incident_evidence as E
from serve.agentic_config import CATALOGUE, CONFIG, PROHIBITED, level_index
from serve.middleware.audit import log_action
from serve.middleware import jwt_auth
from serve.middleware.auth import require_role
from serve.privacy import contains_operator_identity

router = APIRouter(tags=["incident-commander"])


def _deps(request: Request) -> commander.Deps:
    return commander.Deps.from_state(request.app.state, request.app.state.artifacts)


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plant_acl(request: Request, plant_id: str) -> None:
    """PULSETWIN_PLANT_ACL='{"demo-manager": ["plant_a"]}' restricts a principal to named
    plants. Unset = single-tenant demo, every plant visible. The principal is the SERVER's
    idea of who is calling (session or JWT subject), never a header the client sets."""
    raw = os.environ.get("PULSETWIN_PLANT_ACL")
    if not raw:
        return
    acl = json.loads(raw)
    who = (getattr(request.state, "session_user", None)
           or f"role:{getattr(request.state, 'role', 'supervisor')}")
    allowed = acl.get(who, acl.get(f"role:{getattr(request.state, 'role', '')}", None))
    if allowed is not None and plant_id not in allowed:
        raise HTTPException(404, "not found")   # 404, not 403: do not confirm it exists


def _incident(request: Request, incident_id: str) -> dict[str, Any]:
    try:
        inc = commander.get_incident(request.app.state.ops, incident_id)
    except KeyError:
        raise HTTPException(404, f"unknown incident '{incident_id}'")
    _plant_acl(request, inc["plant_id"])
    return inc


def _rec_view(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "options": _loads(r.get("options")) or [], "rationale": _loads(r.get("rationale")),
            "modification": _loads(r.get("modification"))}


def _latest_rec(ops: Any, incident_id: str) -> dict[str, Any] | None:
    r = db.query_one(ops, "SELECT * FROM recommendations WHERE incident_id=? "
                          "ORDER BY created_at DESC LIMIT 1", (incident_id,))
    return _rec_view(r) if r else None


def _investigation_view(ops: Any, incident_id: str) -> dict[str, Any] | None:
    latest = commander.latest_investigation(ops, incident_id)
    if not latest:
        return None
    inv, state = commander.load(ops, latest["investigation_id"])
    steps = db.query_all(ops, "SELECT * FROM investigation_steps WHERE investigation_id=? "
                              "ORDER BY seq", (inv["investigation_id"],))
    for s in steps:
        s["plan"] = _loads(s.get("plan"))
        s["evidence_ids"] = _loads(s.get("evidence_ids")) or []
    runs = db.query_all(ops, "SELECT * FROM agent_runs WHERE investigation_id=? ORDER BY started_at",
                        (inv["investigation_id"],))
    for r in runs:
        r["tools_called"] = _loads(r.get("tools_called")) or []
    return {"investigation_id": inv["investigation_id"], "status": inv["status"],
            "phase": state.get("phase"), "planner_mode": state.get("planner_mode"),
            "version": inv["version"], "created_at": inv["created_at"],
            "finished_at": inv["finished_at"], "state": state, "steps": steps,
            "agent_runs": runs}


# ------------------------------------------------------------------------ incidents
class OpenIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_id: str = "plant_a"
    kind: Literal["throughput", "equipment", "quality", "environment"]
    station_id: int | None = None
    title: str = Field(min_length=4, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)
    sim_run_id: str | None = Field(default=None, max_length=64)
    investigate: bool = True


@router.post("/api/incidents")
def open_incident(req: OpenIncident, request: Request,
                  role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    _plant_acl(request, req.plant_id)
    if any(ord(c) < 32 and c not in "\n\t" for c in req.title + (req.notes or "")):
        raise HTTPException(422, "control characters are not allowed")
    deps = _deps(request)
    try:
        inc = commander.open_incident(deps, plant_id=req.plant_id, kind=req.kind,
                                      station_id=req.station_id, title=req.title,
                                      source="manual", opened_by=role, notes=req.notes,
                                      sim_run_id=req.sim_run_id)
        inv_id = commander.launch(deps, inc["incident_id"], role) if req.investigate else None
    except commander.CommanderError as exc:
        raise HTTPException(422, str(exc))
    log_action(request, deps.ops, role, "incident_opened", "incident", inc["incident_id"],
               {"kind": req.kind, "station_id": inc["station_id"]})
    return {**inc, "investigation_id": inv_id}


@router.get("/api/incidents")
def list_incidents(request: Request, plant_id: str = "plant_a", limit: int = 30,
                   scope: Literal["current", "all"] = "current",
                   role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    """`scope=current` (the default) is the plant's latest simulation run only -- what is
    happening now. `scope=all` is the whole history, for the incident log. Every row says
    how many times the same problem was seen before (`seen_before`), so a repeat reads as
    a repeat instead of as new clutter."""
    _plant_acl(request, plant_id)
    ops = request.app.state.ops
    run = db.query_one(ops, "SELECT run_id, status FROM simulation_runs WHERE plant_id=? "
                            "ORDER BY started_at DESC LIMIT 1", (plant_id,))
    where, params = "plant_id=?", [plant_id]
    if scope == "current":
        where += " AND sim_run_id=?"
        params.append((run or {}).get("run_id") or "")
    rows = db.query_all(ops, f"SELECT * FROM incidents WHERE {where} "
                             "ORDER BY created_at DESC LIMIT ?",
                        (*params, max(1, min(int(limit), 200))))
    history = db.query_all(ops, "SELECT kind, station_id, source_ref, created_at FROM incidents "
                                "WHERE plant_id=?", (plant_id,))
    # Two batched queries, not two per row: the supervisor polls this every 5-10 s and
    # the all-runs log lists 100 rows, and 200 round trips put 10 % of calls over 1 s under
    # a live run (LOOP verifier, 2026-09-28). The latest row per incident wins.
    ids = [r["incident_id"] for r in rows]
    marks = ",".join("?" * len(ids)) or "NULL"
    state_col = ", state" if scope == "current" else ""   # only the floor card needs it
    invs: dict[str, dict[str, Any]] = {}
    for i in db.query_all(ops, "SELECT incident_id, investigation_id, status, phase, assessment, "
                               f"confidence, planner_mode{state_col} FROM investigations "
                               f"WHERE incident_id IN ({marks}) ORDER BY created_at", tuple(ids)):
        invs[i.pop("incident_id")] = i
    recs: dict[str, dict[str, Any]] = {}
    for x in db.query_all(ops, "SELECT incident_id, recommendation_id, status, root_cause_title, "
                               f"confidence, options FROM recommendations WHERE incident_id IN "
                               f"({marks}) ORDER BY created_at", tuple(ids)):
        recs[x.pop("incident_id")] = x
    out = []
    for r in rows:
        inv = invs.get(r["incident_id"])
        state = _loads((inv or {}).pop("state", None)) or {}
        rec = recs.get(r["incident_id"])
        seen = sum(1 for h in history if h["kind"] == r["kind"]
                   and h["station_id"] == r["station_id"] and h["source_ref"] == r["source_ref"]
                   and str(h["created_at"]) < str(r["created_at"]))
        summary = {k: v for k, v in (rec or {}).items() if k != "options"} if rec else None
        out.append({**r, "symptom": _loads(r.get("symptom")) or {}, "investigation": inv,
                    "recommendation": summary, "seen_before": seen,
                    "headline": incident_story.headline(r, rec),
                    # ADR-071: the floor card's "affected vehicles" -- current run only, since
                    # it is a count query per row and the history log does not show it.
                    "affected": (decisions.affected_vehicles(ops, r, state)
                                 if scope == "current" else None)})
    return {"plant_id": plant_id, "scope": scope, "run": run, "incidents": out,
            "count": len(out)}


@router.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: str, request: Request,
                    role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    inc = _incident(request, incident_id)
    ops = request.app.state.ops
    actions = db.query_all(ops, "SELECT * FROM enterprise_actions WHERE incident_id=? "
                                "ORDER BY created_at", (incident_id,))
    for a in actions:
        for k in ("parameters", "audit_metadata", "predicted"):
            a[k] = _loads(a.get(k))
        if a.get("outcome_id"):
            a["outcome"] = db.query_one(ops, "SELECT * FROM action_outcomes WHERE outcome_id=?",
                                        (a["outcome_id"],))
    case = db.query_one(ops, "SELECT * FROM incident_cases WHERE incident_id=?", (incident_id,))
    if case:
        for k in ("symptoms", "hypotheses_considered", "actions_considered",
                  "simulation_prediction", "actual_outcome"):
            case[k] = _loads(case.get(k))
    investigation = _investigation_view(ops, incident_id)
    recommendation = _latest_rec(ops, incident_id)
    return {"incident": inc, "investigation": investigation, "recommendation": recommendation,
            "actions": actions, "case": case,
            "headline": incident_story.headline(inc, recommendation),
            "explanation": incident_story.explain(inc, investigation, recommendation, actions),
            "stages": incident_story.stages(inc, investigation, recommendation, actions)}


class InvestigateRequest(BaseModel):
    mode: Literal["auto", "deterministic", "llm"] = "auto"
    synchronous: bool = False


@router.post("/api/incidents/{incident_id}/investigate")
def investigate(incident_id: str, request: Request, req: InvestigateRequest | None = None,
                role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    inc = _incident(request, incident_id)
    req = req or InvestigateRequest()
    deps = _deps(request)
    mode = req.mode
    if mode == "llm" and commander.default_planner_mode() != "llm":
        mode = "deterministic"   # no key / not enabled: said in the response, not hidden
    inv_id = commander.launch(deps, inc["incident_id"], role, mode=mode)
    if req.synchronous:
        commander.run_to_block(deps, inv_id)
    log_action(request, deps.ops, role, "investigation_started", "investigation", inv_id,
               {"incident_id": incident_id, "mode": mode})
    return {"investigation_id": inv_id, "planner_mode": mode,
            "note": None if mode == req.mode or req.mode == "auto" else
            "AI provider unavailable or LLM planner disabled: running deterministic investigation mode",
            **(_investigation_view(deps.ops, incident_id) or {})}


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dict[str, Any] | None = None
    planner: dict[str, Any] | None = None
    heartbeat_only: bool = False
    fallback: str | None = Field(default=None, max_length=300)


@router.post("/api/incidents/{incident_id}/investigation/continue")
def continue_investigation(incident_id: str, req: ContinueRequest, request: Request,
                           role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    _incident(request, incident_id)
    deps = _deps(request)
    latest = commander.latest_investigation(deps.ops, incident_id)
    if not latest:
        raise HTTPException(409, "no investigation has been started for this incident")
    inv_id = latest["investigation_id"]
    commander.heartbeat(deps.ops, inv_id)
    if req.fallback:
        commander.fall_back_to_deterministic(deps.ops, inv_id, req.fallback)
    elif not req.heartbeat_only:
        meta = {k: v for k, v in (req.planner or {}).items()
                if k in ("provider", "model", "prompt_tokens", "completion_tokens", "cost_usd",
                         "cost_source", "latency_ms")}
        commander.advance(deps, inv_id, plan=req.plan, planner_meta=meta)
    return _investigation_view(deps.ops, incident_id) or {}


@router.get("/api/incidents/{incident_id}/investigation")
def get_investigation(incident_id: str, request: Request,
                      role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    _incident(request, incident_id)
    view = _investigation_view(request.app.state.ops, incident_id)
    if view is None:
        raise HTTPException(404, "no investigation yet")
    return view


@router.get("/api/incidents/{incident_id}/investigation/options")
def investigation_options(incident_id: str, request: Request,
                          role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    """What the commander COULD ask for next, with expected information gain: the
    allowlist an LLM planner is shown, and what the workspace renders as 'next best
    evidence'. Read-only."""
    inc = _incident(request, incident_id)
    latest = commander.latest_investigation(request.app.state.ops, incident_id)
    if not latest:
        raise HTTPException(404, "no investigation yet")
    deps = _deps(request)
    _, state = commander.load(deps.ops, latest["investigation_id"])
    if not state.get("hypotheses"):
        return {"requests": [], "testable": []}
    ctx = commander.context(deps, inc, state)
    return {"requests": commander.candidate_requests(ctx, inc, state),
            "testable": [t["hypothesis"] for t in commander.simulation_tests(ctx, inc, state)]}


@router.get("/api/incidents/{incident_id}/hypotheses")
def get_hypotheses(incident_id: str, request: Request,
                   role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    _incident(request, incident_id)
    view = _investigation_view(request.app.state.ops, incident_id)
    if view is None:
        raise HTTPException(404, "no investigation yet")
    return {"incident_id": incident_id, "hypotheses": view["state"].get("hypotheses", []),
            "scoring": "softmax over ln(prior) + sum(quality x weight); docs/AGENT_ARCHITECTURE.md"}


@router.get("/api/incidents/{incident_id}/recommendations")
def get_recommendations(incident_id: str, request: Request,
                        role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    _incident(request, incident_id)
    rows = db.query_all(request.app.state.ops, "SELECT * FROM recommendations WHERE incident_id=? "
                                               "ORDER BY created_at DESC", (incident_id,))
    return {"incident_id": incident_id, "recommendations": [_rec_view(r) for r in rows]}


@router.get("/api/incidents/{incident_id}/similar-cases")
def similar_cases(incident_id: str, request: Request,
                  role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    inc = _incident(request, incident_id)
    topo = E.topology(request.app.state.main_con, inc["plant_id"])
    return {"incident_id": incident_id,
            "cases": case_memory.similar(request.app.state.ops, request.app.state.main_con,
                                         inc, topo, limit=5),
            "retrieval": "structured filter (kind) + structured score (zone, station role, "
                         "plant) + TF-IDF cosine; descriptive only, never a policy input"}


# ------------------------------------------------------------------------ decisions
def _recommendation(request: Request, rec_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    r = db.query_one(request.app.state.ops, "SELECT * FROM recommendations WHERE "
                                            "recommendation_id=?", (rec_id,))
    if r is None:
        raise HTTPException(404, f"unknown recommendation '{rec_id}'")
    inc = _incident(request, r["incident_id"])
    return _rec_view(r), inc


def _state_for(ops: Any, rec: dict[str, Any]) -> dict[str, Any]:
    _, state = commander.load(ops, rec["investigation_id"])
    return state


def _check_note(note: str | None) -> None:
    if note and (contains_operator_identity(note) or any(ord(c) < 32 and c not in "\n\t" for c in note)):
        raise HTTPException(422, "the note must not identify an individual operator or contain "
                                 "control characters")


def _govern_for_caller(ops: Any, rec: dict[str, Any], option: dict[str, Any], role: str,
                       state: dict[str, Any], incident_id: str) -> dict[str, Any]:
    a = state.get("assessment") or {}
    exp = (option.get("expected_impact") or {})
    g = governor.evaluate(option["action_type"], assessment=a.get("assessment", "LOW_CONFIDENCE"),
                          confidence=float(a.get("confidence") or 0.0),
                          sensor_ok=state.get("sensor_ok", True),
                          model_status=(state.get("model_health") or {}).get("status", "healthy"),
                          simulation=({"verdict": "improved" if exp.get("improves") else "no_improvement"}
                                      if exp.get("basis") == "SIMULATED" else None),
                          cost_usd=(option.get("cost") or {}).get("value_usd"), role=role)
    db.execute(ops, """INSERT INTO autonomy_decisions
        (decision_id, created_at, incident_id, recommendation_id, option_id, action_type,
         decision, required_level, granted_level, evaluated_for_role, inputs, reasons)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("AD-" + os.urandom(5).hex(), _now(), incident_id, rec["recommendation_id"],
         option["option_id"], option["action_type"], g["decision"], g["required_level"],
         g["granted_level"], role, json.dumps(g["inputs"]), json.dumps(g["reasons"])))
    return g


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=500)


def _approve(request: Request, rec: dict[str, Any], inc: dict[str, Any], option: dict[str, Any],
             role: str, note: str | None, status: str) -> dict[str, Any]:
    ops = request.app.state.ops
    if option["action_type"] in PROHIBITED:
        raise HTTPException(403, f"'{option['action_type']}' is a prohibited OT/safety action")
    if CATALOGUE.get(option["action_type"], {}).get("adapter") == "none":
        # "Continue monitoring" coordinates nothing: the decision is recorded, no action row.
        db.execute(ops, "UPDATE recommendations SET status=?, selected_option_id=?, "
                        "decided_by_role=?, decided_at=?, decision_note=?, updated_at=? "
                        "WHERE recommendation_id=?",
                   (status, option["option_id"], role, _now(), note, _now(), rec["recommendation_id"]))
        case_memory.record_decision(ops, inc["incident_id"], status, note, option["action_type"])
        commander._set_incident_status(ops, inc["incident_id"], "monitoring")
        log_action(request, ops, role, f"recommendation_{status}", "recommendation",
                   rec["recommendation_id"], {"option_id": option["option_id"]})
        return {"recommendation_id": rec["recommendation_id"], "status": status, "action": None}
    state = _state_for(ops, rec)
    g = _govern_for_caller(ops, rec, option, role, state, inc["incident_id"])
    if g["decision"] not in ("HUMAN_APPROVAL_REQUIRED", "AUTO_EXECUTE_ALLOWED"):
        raise HTTPException(403, f"autonomy governor: {g['decision']} for role '{role}' -- "
                                 + "; ".join(g["reasons"]))
    try:
        action = G.request_action(ops, incident=inc, recommendation_id=rec["recommendation_id"],
                                  option=option, requested_by="commander",
                                  approved_by=getattr(request.state, "session_user", None) or role,
                                  autonomy_level="L3")
    except G.ActionRejected as exc:
        raise HTTPException(422, str(exc))
    db.execute(ops, "UPDATE recommendations SET status=?, selected_option_id=?, decided_by_role=?, "
                    "decided_by_user=?, decided_at=?, decision_note=?, updated_at=? "
                    "WHERE recommendation_id=?",
               (status, option["option_id"], role, getattr(request.state, "session_user", None),
                _now(), note, _now(), rec["recommendation_id"]))
    case_memory.record_decision(ops, inc["incident_id"], status, note, option["action_type"])
    commander._set_incident_status(ops, inc["incident_id"], "action_approved")
    log_action(request, ops, role, f"recommendation_{status}", "recommendation",
               rec["recommendation_id"], {"option_id": option["option_id"],
                                          "action_type": option["action_type"],
                                          "action_id": action["action_id"],
                                          "governor": g["decision"]})
    return {"recommendation_id": rec["recommendation_id"], "status": status,
            "action": action, "governor": g}


DECIDABLE = ("proposed", "withheld", "no_action")
DECIDE_LEVEL = "L3"   # what a human approval requires (governor: HUMAN_APPROVAL_REQUIRED)


def _require_decision_authority(role: str) -> None:
    """Rejecting or sending back a recommendation is a decision on it, so it needs the same
    authority approving does. The role HIERARCHY alone let leadership (ceiling L2, below the
    L3 an approval needs) reject and re-open investigations that it could not approve --
    found by the loop's independent verifier, 2026-09-28."""
    ceiling = CONFIG["autonomy"]["role_ceiling"].get(role, "L0")
    if level_index(ceiling) < level_index(DECIDE_LEVEL):
        raise HTTPException(403, f"deciding on a recommendation needs authority {DECIDE_LEVEL}; "
                                 f"{role} holds {ceiling}")


@router.post("/api/recommendations/{rec_id}/approve")
def approve(rec_id: str, req: ApproveRequest, request: Request,
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    rec, inc = _recommendation(request, rec_id)
    _check_note(req.note)
    if rec["status"] not in DECIDABLE:
        raise HTTPException(409, f"recommendation is already {rec['status']}")
    option = next((o for o in rec["options"] if o["option_id"] == req.option_id), None)
    if option is None:
        raise HTTPException(404, f"no option '{req.option_id}' on this recommendation")
    return _approve(request, rec, inc, option, role, req.note, "approved")


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


@router.post("/api/recommendations/{rec_id}/reject")
def reject(rec_id: str, req: RejectRequest, request: Request,
           role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    _require_decision_authority(role)
    rec, inc = _recommendation(request, rec_id)
    _check_note(req.reason)
    if rec["status"] not in DECIDABLE:
        raise HTTPException(409, f"recommendation is already {rec['status']}")
    ops = request.app.state.ops
    db.execute(ops, "UPDATE recommendations SET status='rejected', decided_by_role=?, "
                    "decided_by_user=?, decided_at=?, decision_note=?, updated_at=? "
                    "WHERE recommendation_id=?",
               (role, getattr(request.state, "session_user", None), _now(), req.reason, _now(), rec_id))
    # The reason becomes operational memory: the next similar case reads it.
    case_memory.record_decision(ops, inc["incident_id"], "rejected", req.reason, None)
    commander._set_incident_status(ops, inc["incident_id"], "recommendation_rejected")
    log_action(request, ops, role, "recommendation_rejected", "recommendation", rec_id,
               {"incident_id": inc["incident_id"]})
    return {"recommendation_id": rec_id, "status": "rejected"}


class ModifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str = Field(min_length=1, max_length=20)
    schedule_window_id: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=300)
    priority: Literal["low", "normal", "high"] | None = None
    note: str = Field(min_length=3, max_length=500)


@router.post("/api/recommendations/{rec_id}/modify")
def modify(rec_id: str, req: ModifyRequest, request: Request,
           role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """Approve an option with human edits -- only to fields the adapter validates (the
    schedule and the work description). The action type and target never change here:
    a different action is a different recommendation."""
    rec, inc = _recommendation(request, rec_id)
    _check_note(req.note)
    _check_note(req.description)
    if rec["status"] not in DECIDABLE:
        raise HTTPException(409, f"recommendation is already {rec['status']}")
    option = next((o for o in rec["options"] if o["option_id"] == req.option_id), None)
    if option is None:
        raise HTTPException(404, f"no option '{req.option_id}' on this recommendation")
    params = dict(option.get("parameters") or {})
    if req.description:
        params["description"] = req.description
    if req.priority:
        params["priority"] = req.priority
    if req.schedule_window_id:
        plan = option.get("maintenance_plan") or {}
        window = next((o["window"] for o in plan.get("options", [])
                       if o.get("window") and o["window"]["window_id"] == req.schedule_window_id), None)
        if window is None:
            raise HTTPException(422, f"window '{req.schedule_window_id}' is not one of this "
                                     "option's evaluated windows")
        params["schedule"] = window
    modified = {**option, "parameters": params, "option_id": f"{option['option_id']}-M"}
    db.execute(request.app.state.ops, "UPDATE recommendations SET modification=? WHERE "
                                      "recommendation_id=?",
               (json.dumps({"from": option["option_id"], "parameters": params}), rec_id))
    return _approve(request, rec, inc, modified, role, req.note, "modified")


class MoreInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=3, max_length=500)
    # Bounded focus areas (commander.FOCUS_PROBES); the note is the manager's words, kept as
    # data for the record and for an LLM planner -- never executed.
    focus: list[Literal["more_evidence", "supplier_lot"]] = Field(default_factory=list, max_length=2)


@router.post("/api/recommendations/{rec_id}/request-investigation")
def request_investigation(rec_id: str, req: MoreInvestigationRequest, request: Request,
                          role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    _require_decision_authority(role)
    rec, inc = _recommendation(request, rec_id)
    _check_note(req.note)
    if rec["status"] not in DECIDABLE:
        raise HTTPException(409, f"recommendation is already {rec['status']}")
    ops = request.app.state.ops
    db.execute(ops, "UPDATE recommendations SET status='more_investigation', decided_by_role=?, "
                    "decided_at=?, decision_note=?, updated_at=? WHERE recommendation_id=?",
               (role, _now(), req.note, _now(), rec_id))
    case_memory.record_decision(ops, inc["incident_id"], "more_investigation", req.note, None)
    # A fresh investigation on the SAME incident: a later trigger point (more telemetry),
    # the same budgets. The note is stored as data for the human record, never a prompt.
    last = db.query_one(ops, "SELECT MAX(seq) AS s FROM simulation_events WHERE run_id=?",
                        (inc["sim_run_id"],))
    if last and last.get("s"):
        row = db.query_one(ops, "SELECT payload FROM simulation_events WHERE run_id=? AND seq=?",
                           (inc["sim_run_id"], last["s"]))
        ts = (_loads(row["payload"]) or {}).get("ts") if row else None
        db.execute(ops, "UPDATE incidents SET trigger_seq=?, trigger_ts=COALESCE(?, trigger_ts) "
                        "WHERE incident_id=?", (int(last["s"]), ts, inc["incident_id"]))
    prior = db.query_one(ops, "SELECT state FROM investigations WHERE investigation_id=?",
                         (rec["investigation_id"],))
    prior_top = ((_loads((prior or {}).get("state")) or {}).get("hypotheses") or [{}])[0]
    inv_id = commander.launch(_deps(request), inc["incident_id"], role, manager_request={
        "instruction": req.note, "focus": list(req.focus), "requested_by": role,
        "prior_investigation_id": rec["investigation_id"],
        "prior_conclusion": {"title": prior_top.get("title"), "confidence": prior_top.get("confidence")}})
    log_action(request, ops, role, "investigation_requested", "recommendation", rec_id,
               {"investigation_id": inv_id, "focus": list(req.focus)})
    return {"recommendation_id": rec_id, "status": "more_investigation", "investigation_id": inv_id}


# ------------------------------------------------------------------------ actions
def _action(request: Request, action_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    a = db.query_one(request.app.state.ops, "SELECT * FROM enterprise_actions WHERE action_id=?",
                     (action_id,))
    if a is None:
        raise HTTPException(404, f"unknown action '{action_id}'")
    return a, _incident(request, a["incident_id"])


def _view_action(a: dict[str, Any]) -> dict[str, Any]:
    return {**a, **{k: _loads(a.get(k)) for k in ("parameters", "audit_metadata", "predicted")}}


@router.post("/api/actions/{action_id}/execute")
def execute_action(action_id: str, request: Request,
                   role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    if role not in CONFIG["autonomy"]["execute_roles"]:
        raise HTTPException(403, f"role '{role}' may not execute enterprise actions "
                                 f"(allowed: {CONFIG['autonomy']['execute_roles']})")
    a, inc = _action(request, action_id)
    ops = request.app.state.ops
    try:
        done = G.execute(ops, a)
    except G.ActionRejected as exc:
        raise HTTPException(409, str(exc))
    outcome_id = None
    if not done.get("replayed"):
        outcome_id = commander.apply_to_twin(_deps(request), done, inc)
        db.execute(ops, "UPDATE recommendations SET status='executed', updated_at=? WHERE "
                        "recommendation_id=?", (_now(), done["recommendation_id"]))
        if outcome_id is None:
            commander._set_incident_status(ops, inc["incident_id"], "action_executed")
        log_action(request, ops, role, "enterprise_action_executed", "enterprise_action",
                   action_id, {"action_type": done["action_type"],
                               "external_reference": done.get("external_reference"),
                               "outcome_id": outcome_id})
    return {"action": _view_action(db.query_one(ops, "SELECT * FROM enterprise_actions WHERE "
                                                     "action_id=?", (action_id,))),
            "replayed": bool(done.get("replayed")), "outcome_id": outcome_id,
            "applied_to_twin": outcome_id is not None}


@router.get("/api/actions/{action_id}")
def action_status(action_id: str, request: Request,
                  role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    a, _ = _action(request, action_id)
    out = _view_action(a)
    if a.get("outcome_id"):
        out["outcome"] = db.query_one(request.app.state.ops, "SELECT * FROM action_outcomes "
                                                             "WHERE outcome_id=?", (a["outcome_id"],))
    return out


@router.post("/api/actions/{action_id}/rollback")
def rollback_action(action_id: str, request: Request,
                    role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    if role not in CONFIG["autonomy"]["execute_roles"]:
        raise HTTPException(403, f"role '{role}' may not roll back enterprise actions")
    a, _ = _action(request, action_id)
    try:
        out = G.rollback(request.app.state.ops, a)
    except G.ActionRejected as exc:
        raise HTTPException(409, str(exc))
    log_action(request, request.app.state.ops, role, "enterprise_action_rolled_back",
               "enterprise_action", action_id, {"rollback_reference": out.get("rollback_reference")})
    return _view_action(out)


# ------------------------------------------------------------------------ planning / ops
@router.get("/api/maintenance/windows")
def maintenance_windows(request: Request, plant_id: str = "plant_a", count: int = 4,
                        role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    _plant_acl(request, plant_id)
    run = request.app.state.live_simulation.active(plant_id) or db.query_one(
        request.app.state.ops, "SELECT simulation_time FROM simulation_runs WHERE plant_id=? "
                               "ORDER BY started_at DESC LIMIT 1", (plant_id,))
    now = (run or {}).get("simulation_time") or (request.app.state.plants.get(plant_id) or {}).get(
        "replay_start_ts")
    if not now:
        raise HTTPException(404, f"unknown plant '{plant_id}'")
    now = str(now).replace(" ", "T")[:26]
    wins = [w for w in (commander.next_window(now, skip=i) for i in range(max(1, min(count, 10)))) if w]
    return {"plant_id": plant_id, "clock": "simulation", "now": now,
            "windows": [{**w, "plant_id": plant_id, "production_loss_cost_usd": 0,
                         "production_loss_basis": "scheduled non-production time"} for w in wins],
            "spare_parts": "not modelled: no inventory system is connected (docs/ENTERPRISE_INTEGRATION.md)"}


@router.get("/api/ai/operations/metrics")
def ai_metrics(request: Request, plant_id: str | None = None,
               role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    if plant_id:
        _plant_acl(request, plant_id)
    return ai_operations.metrics(request.app.state.ops, plant_id)


@router.get("/api/ai/operations/runs")
def ai_runs(request: Request, limit: int = 50, investigation_id: str | None = None,
            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    where, p = ("WHERE investigation_id=?", (investigation_id,)) if investigation_id else ("", ())
    rows = db.query_all(request.app.state.ops, f"SELECT * FROM agent_runs {where} "
                                               "ORDER BY started_at DESC LIMIT ?",
                        (*p, max(1, min(int(limit), 200))))
    for r in rows:
        r["tools_called"] = _loads(r.get("tools_called")) or []
    return {"runs": rows, "count": len(rows)}


@router.get("/api/business/impact")
def business_impact(request: Request, plant_id: str = "plant_a",
                    role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    _plant_acl(request, plant_id)
    if plant_id not in request.app.state.plants:
        raise HTTPException(404, f"unknown plant '{plant_id}'")
    return ai_operations.business_impact(request.app.state.ops, request.app.state.main_con,
                                         request.app.state.plants, plant_id)


@router.get("/api/sensor-maturity")
def sensor_maturity(request: Request, plant_id: str = "plant_a",
                    role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    """Each station's sensing class on the latest run's last 30 simulated minutes."""
    _plant_acl(request, plant_id)
    ops = request.app.state.ops
    run = db.query_one(ops, "SELECT run_id FROM simulation_runs WHERE plant_id=? AND "
                            "event_count > 0 ORDER BY started_at DESC LIMIT 1", (plant_id,))
    topo = E.topology(request.app.state.main_con, plant_id)
    if not topo:
        raise HTTPException(404, f"unknown plant '{plant_id}'")
    if run is None:
        return {"plant_id": plant_id, "run_id": None, "stations": [
            {"station_id": t["station_id"], "station_name": t["station_name"],
             "maturity": "MANUAL_ONLY" if t["sensor_tier"] == "manual" else "UNKNOWN"} for t in topo]}
    last = db.query_one(ops, "SELECT MAX(seq) AS s FROM simulation_events WHERE run_id=?",
                        (run["run_id"],))
    events = E.run_events(ops, run["run_id"], int((last or {}).get("s") or 0))
    end = next((e["ts"] for e in reversed(events) if e.get("ts")), None)
    ctx = E.ProbeContext(plant_id=plant_id, incident={"kind": "throughput"}, topo=topo,
                         base={}, events=events,
                         window=E.in_window(events, end, 1800.0) if end else [], ops=ops,
                         main_con=request.app.state.main_con, rules={}, defect_rate=0.0)
    return {"plant_id": plant_id, "run_id": run["run_id"], "stations": [
        {"station_id": t["station_id"], "station_name": t["station_name"],
         "sensor_tier": t["sensor_tier"], "maturity": E.maturity(ctx, t["station_id"]),
         **{k: v for k, v in E.sensor_completeness(ctx, t["station_id"]).items()
            if k in ("completeness", "dropouts", "events")}} for t in topo]}


@router.get("/api/demo/scenarios")
def demo_catalogue(role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    return {"scenarios": demo_scenarios.catalogue(), "demo_mode": demo_session.enabled()}


class DemoReset(BaseModel):
    plant_id: str = "plant_a"


@router.post("/api/demo/reset")
def demo_reset(request: Request, req: DemoReset | None = None,
               role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    """A fresh demo session: stop the run, close its incidents, expire pending approvals,
    start a healthy run at the next quiet point of the recorded shift. Deletes nothing,
    and a backend without PULSETWIN_DEMO_MODE refuses it (LOOP criterion U)."""
    req = req or DemoReset()
    _plant_acl(request, req.plant_id)
    if not demo_session.enabled():
        raise HTTPException(403, "demo reset is disabled: set PULSETWIN_DEMO_MODE=1 on a demo backend")
    # The anonymous floor view acts as supervisor (ADR-063), which is right for READING the
    # floor but not for ending everyone's session: a reset needs a real sign-in.
    if not jwt_auth.header_grants_role() and not getattr(request.state, "session_user", None):
        raise HTTPException(401, "demo reset requires signing in")
    try:
        return demo_session.reset(request.app.state.ops, request.app.state.live_simulation,
                                  request.app.state.artifacts, req.plant_id, role)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc))


class DemoStart(BaseModel):
    plant_id: str = "plant_a"
    speed: float = Field(default=60.0, gt=0, le=500)


@router.post("/api/demo/scenarios/{key}/start")
def demo_start(key: str, request: Request, req: DemoStart | None = None,
               role: str = Depends(require_role("supervisor"))) -> dict[str, Any]:
    """Open to the floor (ADR-069): the supervisor's simulation controls start runs, and
    POST /api/simulations already accepts any role -- a preset adds no power."""
    req = req or DemoStart()
    _plant_acl(request, req.plant_id)
    try:
        return demo_scenarios.start(_deps(request), key, req.plant_id, req.speed, role)
    except commander.CommanderError as exc:
        raise HTTPException(422, str(exc))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc))
