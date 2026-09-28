"""AI reliability scorecard and business impact, computed from persisted rows (ADR-067).

Every figure here is a query over what the system actually recorded -- incidents,
investigations, steps, recommendations, actions, outcomes, cases, agent runs, triage runs.
Nothing is seeded, nothing is static. A metric with no data is `None` with `n: 0`, never a
plausible default. Each business line carries its BASIS:

    ACTUAL     observed on the running twin (a simulated plant -- said so, every time)
    SIMULATED  a paired twin experiment's projection
    ESTIMATED  a deterministic formula over sourced economics
    PROJECTED  a linear extrapolation forward in time
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np

from serve import db


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _m(value: Any, n: int, basis: str, unit: str = "") -> dict[str, Any]:
    return {"value": value if n else None, "n": n, "basis": basis, "unit": unit}


def _rate(k: int, n: int, basis: str) -> dict[str, Any]:
    return _m(round(k / n, 4) if n else None, n, basis, "ratio")


def _mean(xs: Any) -> float:
    return float(np.mean(list(xs)))


def _median(xs: Any) -> float:
    return float(np.median(list(xs)))


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)


def metrics(ops: Any, plant_id: str | None = None) -> dict[str, Any]:
    where, p = ("WHERE plant_id=?", (plant_id,)) if plant_id else ("", ())
    incidents = db.query_all(ops, f"SELECT incident_id, status, created_at FROM incidents {where}", p)
    ids = {i["incident_id"] for i in incidents}
    invs = [r for r in db.query_all(ops, "SELECT investigation_id, incident_id, status, "
                                         "planner_mode, step_count, created_at, finished_at, "
                                         "state FROM investigations") if r["incident_id"] in ids]
    recs = db.query_all(ops, f"SELECT status, assessment FROM recommendations {where}", p)
    inv_ids = {i["investigation_id"] for i in invs}
    steps = [s for s in db.query_all(ops, "SELECT investigation_id, plan_source, plan_valid, "
                                          "rejection_reason FROM investigation_steps "
                                          "WHERE kind='plan' OR plan_source IS NOT NULL")
             if s["investigation_id"] in inv_ids]
    evidence = [e for e in db.query_all(ops, "SELECT investigation_id, provenance FROM "
                                             "investigation_evidence") if e["investigation_id"] in inv_ids]
    runs = [r for r in db.query_all(ops, "SELECT investigation_id, estimated_cost_usd, "
                                         "fallback_used, latency_ms FROM agent_runs")
            if r["investigation_id"] in inv_ids]
    completed = [i for i in invs if i["status"] == "completed"]
    by_status: dict[str, int] = {}
    for r in recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    decided = sum(by_status.get(k, 0) for k in ("approved", "executed", "rejected", "modified"))
    accepted = sum(by_status.get(k, 0) for k in ("approved", "executed", "modified"))
    llm_steps = [s for s in steps if s["plan_source"] == "llm"]
    invalid = [s for s in llm_steps if s["plan_valid"] == 0]
    invented = [s for s in invalid if "unknown probe" in (s["rejection_reason"] or "")]
    cover = {}
    for e in evidence:
        c = cover.setdefault(e["investigation_id"], [0, 0])
        c[0] += e["provenance"] != "missing"
        c[1] += 1
    latencies = [(_dt(i["finished_at"]) - _dt(i["created_at"])).total_seconds()
                 for i in completed if i["finished_at"] and i["created_at"]]
    llm_invs = [i for i in invs if (_loads(i["state"]) or {}).get("fallbacks")
                or i["planner_mode"] == "llm"]
    fell_back = [i for i in llm_invs if (_loads(i["state"]) or {}).get("fallbacks")]
    det = [i for i in invs if i["planner_mode"] == "deterministic"]
    det_ok = [i for i in det if i["status"] == "completed"]
    outcomes = db.query_all(ops, "SELECT o.status, o.prediction_error, o.expected_change, "
                                 "o.actual_change FROM action_outcomes o JOIN enterprise_actions a "
                                 "ON a.outcome_id = o.outcome_id" + (" WHERE a.plant_id=?" if plant_id else ""), p)
    errs = [abs(float(o["prediction_error"])) for o in outcomes if o["prediction_error"] is not None]
    cases = db.query_all(ops, f"SELECT recurrence, recovery_time_s FROM incident_cases {where}", p)
    rec_times = [float(c["recovery_time_s"]) for c in cases if c["recovery_time_s"] is not None]
    tri = db.query_all(ops, "SELECT status, claims_total, claims_verified FROM triage_runs"
                       + (" WHERE plant_id=?" if plant_id else ""), p)
    served = [t for t in tri if t["status"] in ("verified", "verified_with_redactions", "rejected")]
    claims = sum(int(t["claims_total"] or 0) for t in served)
    unsupported = sum(int(t["claims_total"] or 0) - int(t["claims_verified"] or 0) for t in served)
    costs = [float(r["estimated_cost_usd"] or 0) for r in runs]
    return {
        "plant_id": plant_id,
        "incidents_total": _m(len(incidents), len(incidents) or 1, "incidents", "count"),
        "ai_investigated": _m(len({i["incident_id"] for i in invs}), len(incidents), "investigations", "count"),
        "successful_investigations": _rate(len(completed), len(invs), "investigations.status=completed"),
        "abstention_rate": _rate(by_status.get("withheld", 0) + by_status.get("no_action", 0),
                                 len(recs), "recommendations withheld or no_action"),
        "human_acceptance_rate": _rate(accepted, decided, "recommendations decided by a human"),
        "rejection_rate": _rate(by_status.get("rejected", 0), decided, "recommendations"),
        "modification_rate": _rate(by_status.get("modified", 0), decided, "recommendations"),
        "llm_plan_schema_failure_rate": _rate(len(invalid), len(llm_steps), "investigation_steps (LLM plans)"),
        "invented_tool_rejections": _m(len(invented), len(llm_steps), "investigation_steps", "count"),
        "narrative_unsupported_claim_rate": _rate(unsupported, claims, "triage_runs verifier (GenAI narrative runs)"),
        "narrative_verifier_rejection_rate": _rate(sum(t["status"] == "rejected" for t in served),
                                                   len(served), "triage_runs verifier"),
        "evidence_coverage": _m(round(_mean(c[0] / c[1] for c in cover.values()), 4)
                                if cover else None, len(cover), "investigation_evidence non-missing share", "ratio"),
        "avg_investigation_steps": _m(round(_mean(i["step_count"] or 0 for i in completed), 2)
                                      if completed else None, len(completed), "investigations", "steps"),
        "median_investigation_latency_s": _m(round(_median(latencies), 1) if latencies else None,
                                             len(latencies), "investigations wall clock", "s"),
        "ai_cost_per_incident_usd": _m(round(sum(costs) / max(len({i['incident_id'] for i in invs}), 1), 4)
                                       if runs else None, len(runs), "agent_runs.estimated_cost_usd", "USD"),
        "provider_fallback_rate": _rate(len(fell_back), len(llm_invs), "LLM-planned investigations"),
        "degraded_mode_success_rate": _rate(len(det_ok), len(det), "deterministic-planner investigations"),
        "simulation_prediction_abs_error": _m(round(_mean(errs), 4) if errs else None, len(errs),
                                              "action_outcomes.prediction_error (commander actions)",
                                              "stalled-share points"),
        "incident_recurrence_rate": _rate(sum(1 for c in cases if (c["recurrence"] or 0) > 0), len(cases),
                                          "incident_cases.recurrence"),
        "mean_time_to_resolution_s": _m(round(_mean(rec_times), 1) if rec_times else None,
                                        len(rec_times), "incident_cases.recovery_time_s (simulated clock)", "s"),
        "recommendations_by_status": by_status,
    }


def _econ(main_con: Any, plant_id: str) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in db.query_all(main_con, "SELECT key, value, unit, source FROM "
                                                        "economics WHERE plant_id=?", (plant_id,))}


def business_impact(ops: Any, main_con: Any, plants: dict[str, dict[str, Any]],
                    plant_id: str) -> dict[str, Any]:
    econ = _econ(main_con, plant_id)
    uph = float((plants.get(plant_id) or {}).get("achieved_uph") or 0) or None
    per_h = econ.get("downtime_cost_per_hour")
    unit_value = (float(per_h["value"]) / uph) if per_h and uph else None
    src_unit = (f"downtime_cost_per_hour / achieved UPH ({per_h['source'][:60]})" if per_h else None)
    actions = db.query_all(ops, "SELECT * FROM enterprise_actions WHERE plant_id=?", (plant_id,))
    executed = [a for a in actions if a["status"] == "executed"]
    lines = []
    gains = []
    for a in executed:
        pred = (_loads(a.get("predicted")) or {}).get("throughput_uph") or {}
        if pred.get("mean") is not None and (pred.get("ci_low") or 0) > 0:
            gains.append(float(pred["mean"]))
    lines.append({"metric": "throughput_recovered_uph", "value": round(sum(gains), 3) if gains else None,
                  "n": len(gains), "basis": "SIMULATED",
                  "note": "sum of paired-experiment gains for executed actions whose interval excluded zero"})
    lines.append({"metric": "throughput_recovered_value_usd_per_hour",
                  "value": round(sum(gains) * unit_value, 2) if gains and unit_value else None,
                  "n": len(gains), "basis": "ESTIMATED", "source": src_unit})
    outs = db.query_all(ops, "SELECT o.* FROM action_outcomes o JOIN enterprise_actions a ON "
                             "a.outcome_id=o.outcome_id WHERE a.plant_id=?", (plant_id,))
    done = [o for o in outs if o["status"] not in ("observing", "cancelled")]
    lines.append({"metric": "actions_with_measured_outcome", "value": len(done), "n": len(done),
                  "basis": "ACTUAL",
                  "by_status": {s: sum(o["status"] == s for o in done) for s in
                                {o["status"] for o in done}},
                  "note": "observed on the running digital twin -- a simulated plant, not a physical line"})
    contained = 0
    held_n = 0
    for a in executed:
        if a["action_type"] != "quality_hold":
            continue
        units = (_loads(a.get("parameters")) or {}).get("units") or []
        held_n += len(units)
        inc = db.query_one(ops, "SELECT sim_run_id FROM incidents WHERE incident_id=?", (a["incident_id"],))
        if units and inc and inc.get("sim_run_id"):
            marks = ",".join("?" * len(units))
            rows = db.query_all(ops, f"SELECT payload FROM simulation_events WHERE run_id=? AND "
                                     f"event_type='inspection_outcome' AND unit_id IN ({marks})",
                                (inc["sim_run_id"], *units))
            contained += sum(int((_loads(r["payload"]) or {}).get("actual_defect") or 0) for r in rows)
    lines.append({"metric": "defective_units_contained", "value": contained if held_n else None,
                  "n": held_n, "basis": "ACTUAL",
                  "note": "held units later confirmed defective at the gate (twin run)"})
    esc, rw = econ.get("warranty_escape_cost_per_unit"), econ.get("rework_cost_per_unit")
    lines.append({"metric": "containment_value_usd",
                  "value": round(contained * (float(esc["value"]) - float(rw["value"])), 0)
                  if held_n and esc and rw else None, "n": held_n, "basis": "ESTIMATED",
                  "source": "(warranty_escape_cost_per_unit - rework_cost_per_unit) x contained, "
                            "configs/economics.yaml" if esc and rw else None})
    from serve.agentic_config import CATALOGUE
    moved = [a for a in actions if a["status"] in ("approved", "executed")
             and (_loads(a.get("parameters")) or {}).get("schedule")
             and CATALOGUE.get(a["action_type"], {}).get("downtime_min")]
    minutes = sum(CATALOGUE[a["action_type"]]["downtime_min"] for a in moved)
    lines.append({"metric": "unplanned_downtime_avoided_min", "value": minutes if moved else None,
                  "n": len(moved), "basis": "PROJECTED",
                  "note": "maintenance scheduled into a window instead of an unplanned stop; "
                          "durations are docs/ASSUMPTIONS.md assumptions"})
    lines.append({"metric": "unplanned_downtime_avoided_usd",
                  "value": round(minutes / 60 * float(per_h["value"]), 0) if moved and per_h else None,
                  "n": len(moved), "basis": "ESTIMATED",
                  "source": "configs/economics.yaml downtime_cost_per_hour" if per_h else None})
    no_action = db.query_one(ops, "SELECT COUNT(*) AS n FROM recommendations WHERE plant_id=? AND "
                                  "status='no_action'", (plant_id,))
    k = int((no_action or {}).get("n") or 0)
    alarm = econ.get("supervisor_response_cost_per_alarm")
    lines.append({"metric": "unnecessary_interventions_avoided", "value": k, "n": k, "basis": "ACTUAL",
                  "note": "investigations that found no fault and raised no work order"})
    lines.append({"metric": "unnecessary_intervention_cost_avoided_usd",
                  "value": round(k * float(alarm["value"]), 0) if k and alarm else None, "n": k,
                  "basis": "ESTIMATED",
                  "source": "supervisor_response_cost_per_alarm, configs/economics.yaml" if alarm else None})
    lines.append({"metric": "investigation_time_reduced", "value": None, "n": 0, "basis": "NOT_MEASURED",
                  "note": "no manual-investigation baseline has been recorded; not estimated"})
    return {"plant_id": plant_id, "lines": lines,
            "unit_value_usd": {"value": round(unit_value, 2) if unit_value else None,
                               "basis": "ESTIMATED", "source": src_unit}}
