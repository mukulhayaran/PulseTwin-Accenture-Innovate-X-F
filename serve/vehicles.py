"""The supervisor's Vehicle Timeline (ADR-071): every unit on the current run, and one
unit's full story -- built only from records the system already keeps.

Sources, and the badge each becomes (measured and inferred never share a badge):
  simulation_events station_update   SYSTEM   (entered station, cycle, state)
                                     SENSOR   (a measured torque / weld / paint reading)
                                     INFERRED (a soft-sensor estimate -- never shown as SENSOR)
                                     MANUAL   (a manual station's checklist record)
                                     AI       (the defect-risk model's score crossing the alert line)
  simulation_events inspection_outcome  VERIFIED (the final gate's verdict)
  incidents + investigations         AI       (the Incident Commander's structured findings)
  recommendations / enterprise_actions / action_outcomes
                                     MANAGER / SYSTEM (decision, work order, measured result)
  active_alerts / containment_decisions  MANUAL (floor acknowledgements and decisions)

Operator identity never leaves this module: events carry an operator bucket in the raw
payload and it is not copied into anything returned here.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from serve import db
from serve import incident_evidence as E

SIGNAL_WORDS = {"torque_nm": ("torque", "Nm"), "weld_current_a": ("weld current", "A"),
                "vibration_mm_s": ("vibration", "mm/s"), "humidity_pct": ("humidity", "%"),
                "temperature_c": ("temperature", "°C"), "film_thickness_um": ("film thickness", "µm")}
STATUSES = ("normal", "at_risk", "inspection_required", "incident", "completed")
OPEN_INCIDENT = ("open", "investigating", "awaiting_decision", "needs_manual_check",
                 "action_approved", "action_executed", "monitoring")


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _ts(v: Any) -> str:
    return str(v).replace(" ", "T")[:26] if v is not None else ""


def latest_run(ops: Any, plant_id: str) -> dict[str, Any] | None:
    return db.query_one(ops, "SELECT run_id, status, simulation_time FROM simulation_runs WHERE "
                             "plant_id=? ORDER BY started_at DESC LIMIT 1", (plant_id,))


def _held_units(ops: Any, run_id: str) -> dict[str, dict[str, Any]]:
    """unit -> the quality-hold action that holds it (approved or executed)."""
    out: dict[str, dict[str, Any]] = {}
    for a in db.query_all(ops, "SELECT a.* FROM enterprise_actions a JOIN incidents i ON "
                               "i.incident_id=a.incident_id WHERE i.sim_run_id=? AND "
                               "a.action_type='quality_hold' AND a.status IN "
                               "('approved','executing','executed')", (run_id,)):
        for u in (_loads(a.get("parameters")) or {}).get("units") or []:
            out[u] = a
    return out


def incident_involvement(ops: Any, run_id: str, only_open: bool = True) -> dict[str, list[str]]:
    """unit -> incident ids it is involved in: it passed the incident's station inside the
    incident's evidence window, or the genealogy trace named it. A plain, stated rule."""
    rows = db.query_all(ops, "SELECT i.*, v.state AS inv_state FROM incidents i LEFT JOIN "
                             "investigations v ON v.incident_id=i.incident_id WHERE i.sim_run_id=?",
                        (run_id,))
    out: dict[str, list[str]] = {}
    for r in rows:
        if only_open and r["status"] not in OPEN_INCIDENT:
            continue
        state = _loads(r.get("inv_state")) or {}
        w = state.get("window") or {}
        units: set[str] = set()
        if r.get("station_id") is not None and w.get("start_ts") and w.get("end_ts") and r["kind"] != "quality":
            for e in db.query_all(ops, "SELECT DISTINCT unit_id FROM simulation_events WHERE "
                                       "run_id=? AND station_id=? AND seq<=? AND "
                                       "event_type IN ('station_update','alert') AND ts>=? AND ts<=?",
                                  (run_id, int(r["station_id"]), int(w.get("end_seq") or 10**9),
                                   w["start_ts"], w["end_ts"])):
                if e["unit_id"]:
                    units.add(e["unit_id"])
        for trace in (state.get("traces") or {}).values():
            units.update(trace.get("potentially_affected_units") or [])
        for u in units:
            out.setdefault(u, []).append(r["incident_id"])
    return out


def vehicle_status(row: dict[str, Any], threshold: float, held: set[str],
                   involved: dict[str, list[str]]) -> str:
    u = row["unit_id"]
    if row.get("inspected"):
        return "completed"
    if u in held:
        return "inspection_required"
    if u in involved:
        return "incident"
    if row.get("risk") is not None and float(row["risk"]) >= threshold:
        return "at_risk"
    return "normal"


def list_vehicles(ops: Any, main_con: Any, plants: dict[str, Any], plant_id: str, *,
                  run_id: str | None, status: str | None, q: str | None, limit: int,
                  offset: int) -> dict[str, Any]:
    run = (db.query_one(ops, "SELECT run_id, status, simulation_time FROM simulation_runs "
                             "WHERE run_id=? AND plant_id=?", (run_id, plant_id))
           if run_id else latest_run(ops, plant_id))
    if run is None:
        return {"run": None, "vehicles": [], "total": 0, "counts": {s: 0 for s in STATUSES}}
    threshold = float(plants[plant_id]["budget_threshold"])
    rows = db.query_all(ops, "SELECT unit_id, current_station, status AS station_state, risk, "
                             "variant, lot_id, updated_at, inspected, actual_defect FROM "
                             "unit_current_state WHERE run_id=?", (run["run_id"],))
    held = set(_held_units(ops, run["run_id"]))
    involved = incident_involvement(ops, run["run_id"])
    topo = E.topology(main_con, plant_id)
    index = {t["station_id"]: t for t in topo}
    for r in rows:
        r["status"] = vehicle_status(r, threshold, held, involved)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    if status and status != "all":
        rows = [r for r in rows if r["status"] == status]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["unit_id"].lower()
                or needle == (r.get("variant") or "").lower()
                or needle.lstrip("s") == str(r.get("current_station"))
                or needle == (r.get("lot_id") or "").lower()]
    # The most important first: problems, then newest vehicle first. A STABLE secondary
    # order: sorting by last movement reshuffled most of a live page every refresh.
    order = {"inspection_required": 0, "incident": 1, "at_risk": 2, "normal": 3, "completed": 4}
    rows.sort(key=lambda r: r["unit_id"], reverse=True)
    rows.sort(key=lambda r: order[r["status"]])
    total = len(rows)
    page = rows[offset:offset + limit]
    entered = {}
    if page:
        marks = ",".join("?" * len(page))
        entered = {e["unit_id"]: _ts(e["t"]) for e in db.query_all(
            ops, f"SELECT unit_id, MIN(ts) AS t FROM simulation_events WHERE run_id=? AND "
                 f"unit_id IN ({marks}) GROUP BY unit_id",
            (run["run_id"], *[r["unit_id"] for r in page]))}
    out = []
    for r in page:
        st = index.get(r["current_station"]) or {}
        remaining = sum(t["nominal_cycle_s"] for t in topo if t["index"] > st.get("index", 10**6))
        eta = None
        if not r.get("inspected") and r.get("updated_at"):
            eta = datetime.fromtimestamp(_epoch(r["updated_at"]) + remaining).isoformat()
        out.append({
            "unit_id": r["unit_id"], "variant": r.get("variant"), "lot_id": r.get("lot_id"),
            "current_station": r.get("current_station"),
            "current_station_name": st.get("station_name"), "status": r["status"],
            # share of the line behind the vehicle, for the row's progress bar
            "progress": (1.0 if r.get("inspected") else
                         round((st["index"] + 1) / len(topo), 3) if st else None),
            "risk": r.get("risk"), "risk_threshold": threshold,
            "entered_at": entered.get(r["unit_id"]), "updated_at": _ts(r.get("updated_at")),
            "eta": eta, "eta_basis": "estimated: last event + remaining stations' nominal cycle times",
            "inspected": bool(r.get("inspected")),
            "defect_found": bool(r.get("actual_defect")) if r.get("inspected") else None,
            "incidents": involved.get(r["unit_id"], []),
        })
    return {"run": run, "vehicles": out, "total": total, "counts": counts,
            "limit": limit, "offset": offset}


def _epoch(v: Any) -> float:
    try:
        return E._ts(_ts(v)).timestamp()
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------------ one vehicle
def _event(ts: str, clock: str, source: str, category: str, text: str,
           station: int | None = None, **extra: Any) -> dict[str, Any]:
    return {"ts": ts, "clock": clock, "source": source, "category": category, "text": text,
            "station_id": station, **extra}


def timeline(ops: Any, main_con: Any, artifacts: Any, plants: dict[str, Any], plant_id: str,
             unit_id: str, run_id: str | None) -> dict[str, Any] | None:
    run = (db.query_one(ops, "SELECT run_id FROM simulation_runs WHERE run_id=? AND plant_id=?",
                        (run_id, plant_id)) if run_id else latest_run(ops, plant_id))
    if run is None:
        return None
    rid = run["run_id"]
    state = db.query_one(ops, "SELECT unit_id, current_station, status AS station_state, risk, "
                              "variant, lot_id, updated_at, inspected, actual_defect FROM "
                              "unit_current_state WHERE run_id=? AND unit_id=?", (rid, unit_id))
    if state is None:
        return None
    threshold = float(plants[plant_id]["budget_threshold"])
    topo = {t["station_id"]: t for t in E.topology(main_con, plant_id)}
    base = E.baseline(str(artifacts), plant_id)
    rows = db.query_all(ops, "SELECT seq, payload FROM simulation_events WHERE run_id=? AND "
                             "unit_id=? ORDER BY seq", (rid, unit_id))
    events: list[dict[str, Any]] = []
    risk_flagged = False
    first_ts = None
    for row in rows:
        p = _loads(row["payload"]) or {}
        ts = p.get("ts") or ""
        sid = p.get("station_id")
        name = (topo.get(sid) or {}).get("station_name", "")
        if p.get("event_type") == "inspection_outcome":
            events.append(_event(ts, "simulation", "VERIFIED", "quality",
                                 "Final inspection: DEFECT FOUND" if p.get("actual_defect")
                                 else "Final inspection: passed", sid))
            continue
        if p.get("event_type") not in ("station_update", "alert"):
            continue
        first_ts = first_ts or ts
        state_word = p.get("station_state")
        text = f"At S{sid} {name}: cycle {p.get('cycle_time_s')} s"
        if state_word in ("blocked", "down", "starved"):
            text += f" -- station {state_word}"
        events.append(_event(ts, "simulation", "SYSTEM", "journey", text, sid,
                             station_state=state_word))
        prov = p.get("provenance") or {}
        for sig, (word, unit) in SIGNAL_WORDS.items():
            if p.get(sig) is None:
                continue
            ref = base["signals"].get((sid, sig, str(p.get("variant") or ""))) or \
                base["signals"].get((sid, sig, "*"))
            dev = (float(p[sig]) / ref["mean"] - 1) * 100 if ref and ref["mean"] else None
            inferred = prov.get(sig) == "inferred"
            flag = dev is not None and abs(dev) >= 8
            events.append(_event(
                ts, "simulation", "INFERRED" if inferred else "SENSOR", "process",
                f"{word.capitalize()} {p[sig]} {unit}"
                + (f" ({dev:+.0f}% vs normal)" if dev is not None else "")
                + (" -- estimated, not measured" if inferred else ""),
                sid, flagged=flag))
        if p.get("checklist_time_s") is not None:
            ref = base["checklist"].get(sid)
            norm = f" (norm {ref['mean']:.0f} s)" if ref else ""
            events.append(_event(ts, "simulation", "MANUAL", "manual",
                                 f"Manual checklist completed in {p['checklist_time_s']} s{norm}"
                                 + ("" if p.get("checklist_pass", 1) else " -- FAILED"), sid))
        risk = p.get("calibrated_risk")
        if risk is not None and not risk_flagged and float(risk) >= threshold:
            risk_flagged = True
            events.append(_event(ts, "simulation", "AI", "quality",
                                 f"Defect-risk model flagged this vehicle: {float(risk):.0%} "
                                 f"(alert line {threshold:.0%}) -- a model estimate, not a measurement",
                                 sid))

    # Floor actions on this vehicle (real time).
    for a in db.query_all(ops, "SELECT acknowledged_at, actioned_at, validated_at FROM active_alerts "
                               "WHERE run_id=? AND unit_id=?", (rid, unit_id)):
        if a.get("acknowledged_at"):
            events.append(_event(_ts(a["acknowledged_at"]), "wall", "MANUAL", "manual",
                                 "Alert acknowledged on the floor"))
    for c in db.query_all(ops, "SELECT created_at, selected_action, decided_by_role, status FROM "
                               "containment_decisions WHERE run_id=? AND target_unit_id=?",
                          (rid, unit_id)):
        events.append(_event(_ts(c["created_at"]), "wall", "MANUAL", "manual",
                             f"Containment decision: {c['selected_action']} "
                             f"(by {c['decided_by_role']}, {c.get('status') or 'recorded'})"))
    hold = _held_units(ops, rid).get(unit_id)
    if hold:
        events.append(_event(_ts(hold.get("executed_at") or hold["created_at"]), "wall", "SYSTEM",
                             "manual", f"Placed on quality hold ({hold.get('external_reference') or hold['status']})"))

    incidents = [incident_card(ops, i) for i in incident_involvement(ops, rid, only_open=False).get(unit_id, [])]
    for inc in incidents:
        events.append(_event(inc["trigger_ts"], "simulation", "AI", "ai",
                             f"AI investigation: {inc['title']}", inc.get("station_id"),
                             investigation=inc))
    status = vehicle_status(state, threshold, {unit_id} if hold else set(),
                            {unit_id: [i["incident_id"] for i in incidents
                                       if i["status"] in OPEN_INCIDENT]} if any(
                                i["status"] in OPEN_INCIDENT for i in incidents) else {})
    st = topo.get(state["current_station"]) or {}
    return {"vehicle": {"unit_id": unit_id, "variant": state.get("variant"),
                        "lot_id": state.get("lot_id"), "current_station": state["current_station"],
                        "current_station_name": st.get("station_name"), "status": status,
                        "risk": state.get("risk"), "risk_threshold": threshold,
                        "entered_at": first_ts, "inspected": bool(state.get("inspected")),
                        "defect_found": bool(state.get("actual_defect")) if state.get("inspected") else None,
                        "quality_status": ("Defect found" if state.get("inspected") and state.get("actual_defect")
                                           else "Passed" if state.get("inspected")
                                           else "On quality hold" if hold
                                           else "Inspection required" if status in ("at_risk", "incident")
                                           else "Normal")},
            "run_id": rid, "events": events, "incidents": incidents}


def incident_card(ops: Any, incident_id: str) -> dict[str, Any]:
    """The Incident Commander's STRUCTURED findings for one incident -- observation,
    evidence, hypotheses, status, recommendation and what followed. Persisted fields only;
    no reasoning text beyond what the investigation stored."""
    inc = db.query_one(ops, "SELECT * FROM incidents WHERE incident_id=?", (incident_id,))
    inv = db.query_one(ops, "SELECT investigation_id, status, state FROM investigations WHERE "
                            "incident_id=? ORDER BY created_at DESC LIMIT 1", (incident_id,))
    state = _loads((inv or {}).get("state")) or {}
    ev = {e["evidence_id"]: e for e in db.query_all(
        ops, "SELECT evidence_id, specialist, probe, provenance, summary FROM "
             "investigation_evidence WHERE investigation_id=?",
        ((inv or {}).get("investigation_id") or "",))}
    hyps = state.get("hypotheses") or []
    top = hyps[0] if hyps else None
    evidence = []
    if top:
        for c in sorted(top.get("contributions") or [], key=lambda c: -abs(c["delta"]))[:5]:
            e = ev.get(c["evidence_id"])
            if e:
                evidence.append({"text": e["summary"], "provenance": e["provenance"],
                                 "supports": c["delta"] > 0})
    rec = db.query_one(ops, "SELECT recommendation_id, status, root_cause_title, confidence, "
                            "decided_by_role, decided_at, decision_note, options, "
                            "selected_option_id FROM recommendations WHERE incident_id=? "
                            "ORDER BY created_at DESC LIMIT 1", (incident_id,))
    follow: list[dict[str, Any]] = []
    if rec and rec.get("decided_at"):
        word = {"approved": "approved", "modified": "approved with changes", "executed": "approved",
                "rejected": "rejected", "more_investigation": "sent back for more investigation"}.get(
            rec["status"], rec["status"])
        follow.append(_event(_ts(rec["decided_at"]), "wall", "MANAGER", "incident",
                             f"Manager {word}" + (f": “{rec['decision_note']}”"
                                                  if rec.get("decision_note") else "")))
    for a in db.query_all(ops, "SELECT * FROM enterprise_actions WHERE incident_id=? ORDER BY created_at",
                          (incident_id,)):
        params = _loads(a.get("parameters")) or {}
        if a["status"] in ("executed", "rolled_back"):
            follow.append(_event(_ts(a.get("executed_at")), "wall", "SYSTEM", "incident",
                                 f"{a['action_type'].replace('_', ' ').capitalize()} sent to "
                                 f"{a['adapter'].upper()} ({a.get('external_reference')})"))
        if (params.get("schedule") or {}).get("name"):
            follow.append(_event(_ts(a["created_at"]), "wall", "SYSTEM", "incident",
                                 f"Scheduled for {params['schedule']['name']} "
                                 f"({str(params['schedule'].get('start'))[:16].replace('T', ' ')}, simulated)"))
        if a.get("outcome_id"):
            o = db.query_one(ops, "SELECT status, reason, evaluated_at FROM action_outcomes WHERE "
                                  "outcome_id=?", (a["outcome_id"],))
            if o and o["status"] != "observing":
                follow.append(_event(_ts(o.get("evaluated_at")), "wall", "VERIFIED", "incident",
                                     f"Measured result: {o['status'].replace('_', ' ')} -- {o.get('reason') or ''}"))
            elif o:
                follow.append(_event(_ts(a.get("executed_at")), "wall", "SYSTEM", "incident",
                                     "Watching whether the fix worked"))
    a = state.get("assessment") or {}
    current = state.get("current_step") or {}
    return {
        "incident_id": incident_id, "title": inc["title"], "kind": inc["kind"],
        "station_id": inc.get("station_id"), "status": inc["status"],
        "trigger_ts": _ts(inc.get("trigger_ts")),
        "investigation_status": (inv or {}).get("status"),
        "now": (current.get("reason") if (inv or {}).get("status") not in ("completed", "failed")
                else a.get("reason")),
        "observation": next((ev[i]["summary"] for i in state.get("observations") or [] if i in ev),
                            inc["title"]),
        "evidence": evidence,
        "hypotheses": [{"title": h["title"], "confidence": h["confidence"], "status": h["status"]}
                       for h in hyps[:4]],
        "confidence": a.get("confidence"), "assessment": a.get("assessment"),
        "recommendation": ({"status": rec["status"], "root_cause": rec.get("root_cause_title"),
                            "action": next((o["title"] for o in _loads(rec.get("options")) or []
                                            if o["action_type"] not in ("notify_supervisor",
                                                                         "continue_monitoring")), None)}
                           if rec else None),
        "follow_ups": follow,
    }
