"""The manager's Decision Queue (ADR-071): only what needs the CALLER's authority.

A recommendation is queued for a manager when, and only when:
  * it is `proposed` -- the commander was CONFIDENT and recommends a change (withheld and
    no-action results never reach a manager: those are floor work or nothing at all);
  * it carries an option outside `actions.supervisor_handled` (configs/agentic.yaml) --
    downtime, a quality hold, a line change, an irreversible repair;
  * the autonomy governor, evaluated for the CALLER's role right now, says
    HUMAN_APPROVAL_REQUIRED for that option. A role that cannot authorise it (leadership's
    L2 ceiling) gets an empty queue, not a queue of buttons that 403.

The filtering is server-side; the UI never receives decisions it should not see.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from serve import db, governor
from serve.agentic_config import CATALOGUE, CONFIG

SUPERVISOR_HANDLED = frozenset(CONFIG["actions"]["supervisor_handled"])
METRIC_WORDS = {"throughput_uph": "cars per hour", "blocked_pct": "points of blocked time",
                "down_pct": "points of downtime", "expected_defects": "expected defects",
                "starved_pct": "points of starved time"}
HISTORY = ("approved", "modified", "executed", "rejected", "expired", "more_investigation")


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _state(ops: Any, investigation_id: str) -> dict[str, Any]:
    row = db.query_one(ops, "SELECT state FROM investigations WHERE investigation_id=?",
                       (investigation_id,))
    return _loads((row or {}).get("state")) or {}


def decision_option(rec: dict[str, Any], state: dict[str, Any], role: str
                    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The first option that needs this role's authority, with the governor's verdict."""
    a = state.get("assessment") or {}
    for o in _loads(rec.get("options")) or []:
        if o["action_type"] in SUPERVISOR_HANDLED or o["action_type"] not in CATALOGUE:
            continue
        exp = o.get("expected_impact") or {}
        g = governor.evaluate(
            o["action_type"], assessment=a.get("assessment", "LOW_CONFIDENCE"),
            confidence=float(a.get("confidence") or 0.0), sensor_ok=state.get("sensor_ok", True),
            model_status=(state.get("model_health") or {}).get("status", "healthy"),
            simulation=({"verdict": "improved" if exp.get("improves") else "no_improvement"}
                        if exp.get("basis") == "SIMULATED" else None),
            cost_usd=(o.get("cost") or {}).get("value_usd"), role=role)
        if g["decision"] == "HUMAN_APPROVAL_REQUIRED":
            return o, g
    return None


def affected_vehicles(ops: Any, incident: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """How much production the decision touches, from records: the traced in-flight units
    for a quality incident, else the units that passed the station in the evidence window."""
    traces = state.get("traces") or {}
    if incident["kind"] == "quality" and traces:
        n = max((t.get("potentially_affected_count") or 0) for t in traces.values())
        return {"count": n, "basis": "in-flight units that passed the implicated station after "
                                     "its readings shifted (genealogy trace)"}
    w = state.get("window") or {}
    if incident.get("station_id") is None or not w.get("start_ts"):
        return {"count": None, "basis": "not determinable"}
    row = db.query_one(ops, "SELECT COUNT(DISTINCT unit_id) AS n FROM simulation_events WHERE "
                            "run_id=? AND station_id=? AND seq<=? AND ts>=? AND ts<=?",
                       (incident["sim_run_id"], int(incident["station_id"]),
                        int(w.get("end_seq") or 10**9), w["start_ts"], w["end_ts"]))
    return {"count": int((row or {}).get("n") or 0),
            "basis": f"vehicles through S{incident['station_id']} during the evidence window"}


def _impact(o: dict[str, Any]) -> dict[str, Any] | None:
    exp = o.get("expected_impact") or {}
    d = exp.get("objective_delta") or exp.get("throughput_uph")
    if not d or d.get("mean") is None:
        return None
    metric = exp.get("objective_metric") or "throughput_uph"
    return {"mean": d["mean"], "ci_low": d.get("ci_low"), "ci_high": d.get("ci_high"),
            "metric": METRIC_WORDS.get(metric, metric), "basis": "SIMULATED",
            "experiment_id": exp.get("experiment_id"), "improves": bool(exp.get("improves"))}


def card(ops: Any, rec: dict[str, Any], incident: dict[str, Any], state: dict[str, Any],
         option: dict[str, Any], gov: dict[str, Any]) -> dict[str, Any]:
    plan = option.get("maintenance_plan") or {}
    sched = (option.get("parameters") or {}).get("schedule") or {}
    return {
        "recommendation_id": rec["recommendation_id"], "incident_id": incident["incident_id"],
        "severity": incident.get("severity"), "title": option["title"],
        "problem": incident["title"], "conclusion": rec.get("root_cause_title"),
        "confidence": rec.get("confidence"),
        "affected": affected_vehicles(ops, incident, state),
        "action": {"option_id": option["option_id"], "action_type": option["action_type"],
                   "title": option["title"], "adapter": option.get("adapter"),
                   "station_id": option.get("station_id")},
        "expected_impact": _impact(option), "risk": option.get("risk"),
        "reversible": option.get("reversible"),
        "downtime_min": CATALOGUE[option["action_type"]]["downtime_min"],
        "downtime_basis": "ASSUMPTION (docs/ASSUMPTIONS.md, Enterprise action assumptions)",
        "cost": option.get("cost"), "schedule": sched.get("name"),
        "schedule_windows": [o["window"] for o in plan.get("options", []) if o.get("window")],
        "recommended_timing": plan.get("recommended"),
        "approval": {"required_level": gov["required_level"], "granted_level": gov["granted_level"],
                     "reason": f"{gov['required_level']} action -- manager authorization required",
                     "details": gov["reasons"]},
        "created_at": rec.get("created_at"),
    }


def pending(ops: Any, plant_id: str, role: str) -> list[dict[str, Any]]:
    out = []
    for rec in db.query_all(ops, "SELECT * FROM recommendations WHERE plant_id=? AND status='proposed' "
                                 "ORDER BY created_at DESC", (plant_id,)):
        incident = db.query_one(ops, "SELECT * FROM incidents WHERE incident_id=?", (rec["incident_id"],))
        if incident is None or incident["status"] == "closed":
            continue
        state = _state(ops, rec["investigation_id"])
        picked = decision_option(rec, state, role)
        if picked:
            out.append(card(ops, rec, incident, state, *picked))
    return out


def evidence(ops: Any, rec: dict[str, Any], role: str = "manager") -> dict[str, Any]:
    """WHY IS THE AI RECOMMENDING THIS -- observation, what was investigated, the competing
    explanations, the evidence, the twin tests, the recommendation and why it needs this
    role's approval (LOOP criterion H). Every field is a persisted record or the governor's
    own verdict; nothing composed here that the investigation did not store."""
    state = _state(ops, rec["investigation_id"])
    obs = state.get("observations") or []
    row = (db.query_one(ops, "SELECT summary FROM investigation_evidence WHERE evidence_id=?", (obs[0],))
           if obs else None)
    observation = (row or {}).get("summary")
    steps = db.query_all(ops, "SELECT agent, kind, summary FROM investigation_steps WHERE "
                              "investigation_id=? AND kind IN ('collect','simulate') ORDER BY seq",
                         (rec["investigation_id"],))
    chosen = decision_option(rec, state, role)
    why = _loads(rec.get("rationale")) or {}
    points = [{"text": p["text"], "supports": "supports" in p,
               "against": p.get("contradicts")} for p in why.get("points", []) if not p.get("twin")]
    tests = []
    for x in state.get("experiments") or []:
        if x.get("status") != "done":
            continue
        d = x.get("objective_delta") or {}
        tests.append({"label": x["label"], "tests": x["hypothesis_title"],
                      "metric": METRIC_WORDS.get(x.get("objective_metric"), x.get("objective_metric")),
                      "mean": d.get("mean"), "ci_low": d.get("ci_low"), "ci_high": d.get("ci_high"),
                      "baseline_uph": x.get("baseline_uph"),
                      "throughput_delta": (x.get("throughput_delta") or {}).get("mean"),
                      "effect": x.get("effect"), "experiment_id": x.get("experiment_id")})
    hyps = [{"title": h["title"], "confidence": h["confidence"]} for h in (state.get("hypotheses") or [])[:3]]
    return {"recommendation_id": rec["recommendation_id"], "points": points,
            "twin_tests": tests, "alternatives": hyps[1:], "leading": hyps[:1],
            "conclusion": why.get("therefore"), "assessment": (state.get("assessment") or {}).get("reason"),
            "observation": observation,
            "investigated": [{"specialist": s_["agent"], "what": s_["summary"]} for s_ in steps],
            "explanations": [{"title": h["title"], "confidence": h["confidence"], "status": h["status"]}
                             for h in state.get("hypotheses") or []],
            "recommendation": chosen[0]["title"] if chosen else None,
            "approval": ({"decision": chosen[1]["decision"], "reasons": chosen[1]["reasons"]}
                         if chosen else None)}


def history(ops: Any, plant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(HISTORY))
    out = []
    for rec in db.query_all(ops, f"SELECT * FROM recommendations WHERE plant_id=? AND status IN ({marks}) "
                                 "ORDER BY COALESCE(decided_at, updated_at) DESC LIMIT ?",
                            (plant_id, *HISTORY, limit)):
        inc = db.query_one(ops, "SELECT title, station_id, status FROM incidents WHERE incident_id=?",
                           (rec["incident_id"],)) or {}
        acts = db.query_all(ops, "SELECT action_id, action_type, status, external_reference, outcome_id "
                                 "FROM enterprise_actions WHERE recommendation_id=?",
                            (rec["recommendation_id"],))
        outcome = None
        for a in acts:
            if a.get("outcome_id"):
                o = db.query_one(ops, "SELECT status, reason, prediction_error FROM action_outcomes "
                                      "WHERE outcome_id=?", (a["outcome_id"],))
                outcome = o or outcome
        chosen = next((o for o in _loads(rec.get("options")) or []
                       if o["option_id"] == (rec.get("selected_option_id") or "").split("-M")[0]), None)
        out.append({"recommendation_id": rec["recommendation_id"], "incident_id": rec["incident_id"],
                    "problem": inc.get("title"), "action": (chosen or {}).get("title"),
                    "decision": rec["status"], "decided_by": rec.get("decided_by_role"),
                    "decided_at": rec.get("decided_at") or rec.get("updated_at"),
                    "note": rec.get("decision_note"),
                    "actions": [{"action_type": a["action_type"], "status": a["status"],
                                 "reference": a.get("external_reference")} for a in acts],
                    "outcome": outcome})
    return out


def summary(ops: Any, plant_id: str, queue: list[dict[str, Any]]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    approved_today = db.query_one(
        ops, "SELECT COUNT(*) AS n FROM recommendations WHERE plant_id=? AND status IN "
             "('approved','modified','executed') AND decided_at >= ?", (plant_id, today))
    in_progress = db.query_all(
        ops, "SELECT a.status, o.status AS outcome FROM enterprise_actions a LEFT JOIN action_outcomes o "
             "ON o.outcome_id=a.outcome_id WHERE a.plant_id=?", (plant_id,))
    return {"pending": len(queue),
            "high_risk": sum(1 for c in queue if c.get("risk") in ("medium", "high")
                             or c.get("severity") == "high"),
            "approved_today": int((approved_today or {}).get("n") or 0),
            "in_progress": sum(1 for a in in_progress if a["status"] in ("approved", "executing")
                               or a.get("outcome") == "observing")}
