"""Incidents in plain words, generated from each incident's own stored data (ADR-070).

Deterministic wording, not a language model: every sentence is filled from a persisted
field -- the detector that fired, the evidence rows, the hypothesis confidences, the
governor's decision, the action and its measured outcome -- so the explanation can never
say something the record does not. Two shapes:

  headline(...)  one line for the incident log and the list
  explain(...)   short titled sections for the incident view ("In simple words")
"""

from __future__ import annotations

import json
from typing import Any

WHAT_FIRED: dict[str, str] = {
    "starvation": "the station spent most of its recent cycles stalled (blocked, starved or "
                  "down) while working slower than the line's pace",
    "tool_wear": "torque or weld current over the last few cars drifted at least 8% away from "
                 "normal for that car type",
    "paint_env": "paint-zone humidity rose well above the outside air's humidity",
    "quality_detector": "the final quality gate found more defective cars than the plant's "
                        "normal rate makes likely",
}
SPECIALIST: dict[str, str] = {
    "production": "Production (stalls, cycle time, neighbours, car mix)",
    "reliability": "Reliability (torque / weld signals, breakdown history)",
    "quality": "Quality (defect rate, genealogy trace, lots, checklists, paint climate)",
    "data_quality": "Data quality (sensor health, model-input drift)",
    "memory": "Memory (similar past cases)",
    "simulation": "Digital twin (a simulated test of a fix)",
    "commander": "The alarm itself",
}
METRIC_WORDS: dict[str, str] = {
    "throughput_uph": "cars per hour", "blocked_pct": "points of blocked time",
    "down_pct": "points of downtime", "expected_defects": "expected defects",
    "starved_pct": "points of starved time",
}
PROVENANCE: dict[str, str] = {
    "measured": "measured", "inferred": "estimated", "simulated": "simulated",
    "manual": "from manual records", "missing": "could not be checked",
}


def _pct(v: Any) -> str:
    return f"{round(float(v) * 100)}%" if v is not None else "unknown"


def _options(rec: dict[str, Any] | None) -> list[dict[str, Any]]:
    opts = (rec or {}).get("options") or []
    return json.loads(opts) if isinstance(opts, str) else opts


def headline(incident: dict[str, Any], rec: dict[str, Any] | None) -> str:
    """One readable line: where, what, and where it stands now."""
    where = f"S{incident['station_id']}" if incident.get("station_id") is not None else "the line"
    status = incident.get("status") or "open"
    root = (rec or {}).get("root_cause_title")
    conf = _pct((rec or {}).get("confidence")) if rec else None
    first = next((o for o in _options(rec) if o.get("action_type") not in (
        "notify_supervisor", "continue_monitoring")), None)
    if status in ("open", "investigating"):
        return f"{where}: {incident['title']} -- being investigated."
    if status == "awaiting_decision":
        return (f"{where}: most likely {root} ({conf}). Suggested: "
                f"{(first or {}).get('title', 'see options')}. Waiting for a manager.")
    if status == "needs_manual_check":
        return (f"{where}: cause unclear (best guess {root}, {conf}). "
                "A manual check was requested; no fix recommended.")
    if status == "monitoring":
        return f"{where}: {incident['title']} -- no fault found or fix applied; being monitored."
    if status in ("action_approved", "action_executed"):
        return f"{where}: {root} -- a fix was approved" + (
            " and sent." if status == "action_executed" else "; waiting to be executed.")
    if status == "resolved":
        return f"{where}: {root} -- fixed; the measured result confirmed it."
    if status.startswith("outcome_"):
        return f"{where}: {root} -- fix applied; measured result: {status[8:].replace('_', ' ')}."
    if status == "recommendation_rejected":
        return f"{where}: {root} -- the manager rejected the suggestion (reason kept for next time)."
    if status == "investigation_failed":
        return f"{where}: {incident['title']} -- the investigation failed; see its steps."
    if status == "closed":
        return f"{where}: {incident['title']} -- closed: its run ended before anyone acted."
    return f"{where}: {incident['title']} ({status.replace('_', ' ')})."


def explain(incident: dict[str, Any], investigation: dict[str, Any] | None,
            rec: dict[str, Any] | None, actions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Titled plain-language sections, in the order a person would tell it."""
    out: list[dict[str, str]] = []
    where = f"station S{incident['station_id']}" if incident.get("station_id") is not None else "the line"
    src = incident.get("source")
    if src == "manual":
        what = (f"An operator reported a problem at {where}"
                + (f": “{incident['notes']}”" if incident.get("notes") else "") + ".")
    else:
        why = WHAT_FIRED.get(incident.get("source_ref") or src, "a detector crossed its threshold")
        what = f"An automatic detector raised this at {where} because {why}."
    out.append({"title": "What happened", "text": what})

    state = (investigation or {}).get("state") or {}
    hyps = state.get("hypotheses") or []
    evidence = state.get("evidence") or []
    if not hyps:
        out.append({"title": "Now", "text": "The investigation has not started its analysis yet "
                                            "(it waits for enough fresh data first)."})
        return out
    w = state.get("window") or {}
    span = (f" from {str(w.get('start_ts'))[11:16]} to {str(w.get('end_ts'))[11:16]} (simulated time)"
            if w else "")
    out.append({"title": "What it looked at", "text": (
        f"It took the factory's own event records{span} and listed {len(hyps)} possible "
        f"causes: {', '.join(h['title'] for h in hyps)}. Then, one step at a time, it chose "
        "whichever check would best tell these apart.")})

    by_specialist: dict[str, list[str]] = {}
    for e in evidence:
        # The alarm is "What happened"; twin results get their own "What it tested".
        if e["probe"] in ("trigger", "operator_report", "trace_candidates", "twin_experiment"):
            continue
        tag = PROVENANCE.get(e["provenance"], e["provenance"])
        by_specialist.setdefault(e["specialist"], []).append(f"{e['summary']} ({tag})")
    if by_specialist:
        lines = [f"{SPECIALIST.get(k, k)}: " + "; ".join(v) for k, v in by_specialist.items()]
        out.append({"title": "What it checked", "text": "\n".join(lines)})

    exps = [x for x in state.get("experiments") or [] if x.get("status") == "done"]
    if exps:
        texts = []
        for x in exps:
            d = x.get("objective_delta") or {}
            texts.append(f"It tried “{x['label']}” in the digital twin: "
                         f"{d.get('mean', 0):+.2f} "
                         f"{METRIC_WORDS.get(x.get('objective_metric'), x.get('objective_metric', ''))} "
                         f"(range {d.get('ci_low', 0):+.2f} to {d.get('ci_high', 0):+.2f}), which "
                         f"{x.get('effect', 'was recorded')} “{x['hypothesis_title']}”.")
        out.append({"title": "What it tested", "text": " ".join(texts)})

    top = hyps[0]
    a = state.get("assessment") or {}
    ruled = [h["title"] for h in hyps[1:] if h.get("status") in ("rejected", "weakened")]
    concl = f"The most likely cause is “{top['title']}” at {_pct(top['confidence'])}."
    if not top.get("supporting_evidence"):
        concl += (" Note: no check points directly to it -- it leads because the other causes "
                  "were ruled down.")
    if ruled:
        concl += f" Ruled down: {', '.join(ruled)}."
    mat = a.get("sensor_maturity")
    if mat and mat not in ("HIGH",):
        concl += (f" This station's sensing is {mat.replace('_', ' ').lower()}, so confidence is "
                  f"capped at {_pct(a.get('cap'))}.")
    out.append({"title": "What it concluded", "text": concl})

    status = (rec or {}).get("status")
    opts = _options(rec)
    if status == "withheld" or a.get("assessment") not in (None, "CONFIDENT"):
        manual = [o["title"] for o in opts if o["action_type"] in ("manual_inspection",
                                                                    "sensor_calibration")]
        decision = (f"It will not recommend a fix: {a.get('reason', 'the evidence is not strong enough')}. "
                    "The rule is: below 70% sure, suggest only safe checks. "
                    + (f"It asks for: {'; '.join(manual)}." if manual else ""))
    elif status == "no_action" or top["key"] == "no_fault":
        decision = "It found no real fault, so it raised no work order (this avoids a needless stop)."
    else:
        first = next((o for o in opts if o["action_type"] not in ("notify_supervisor",
                                                                  "continue_monitoring")), None)
        decision = (f"It is confident enough (at least 70%) to suggest: "
                    f"{(first or {}).get('title', 'see the options')}. A manager must approve "
                    "it; the system cannot act on the line by itself.")
    out.append({"title": "What it decided", "text": decision})

    now = {"awaiting_decision": "Waiting for a manager's decision on the Manager page.",
           "needs_manual_check": "Waiting for someone to do the manual check.",
           "recommendation_rejected": "The manager rejected it; the reason is saved and will be "
                                      "shown next time a similar case appears.",
           "closed": "Closed automatically: its simulation run ended before anyone acted.",
           "investigation_failed": "The investigation stopped with an error."}
    text = now.get(incident.get("status") or "")
    for act in actions:
        o = act.get("outcome") or {}
        text = (f"The {act['action_type'].replace('_', ' ')} was {act['status']}"
                + (f" ({act.get('external_reference')})" if act.get("external_reference") else "")
                + ".")
        if o.get("status") == "observing":
            text += " The system is now watching whether it worked."
        elif o.get("status"):
            text += (f" Measured result: {o['status'].replace('_', ' ')} -- "
                     f"{o.get('reason') or ''}".rstrip(" -"))
    if text:
        out.append({"title": "Now", "text": text})
    return out


# LOOP criterion G: the AI's progress as stages, read from the investigation's PERSISTED
# phase, experiments, recommendation and actions -- never from a clock in the browser.
STAGES = (("detect", "Detect"), ("investigate", "Investigate"), ("hypothesize", "Hypothesize"),
          ("simulate", "Simulate"), ("recommend", "Recommend"), ("approve", "Approve"),
          ("verify", "Verify"))
_PHASE_AT = {"observe": 0, "awaiting_telemetry": 0, "plan": 1, "collect": 1,
             "awaiting_simulation": 2, "decide": 3, "completed": 4}


def stages(incident: dict[str, Any], investigation: dict[str, Any] | None,
           rec: dict[str, Any] | None, actions: list[dict[str, Any]]) -> list[dict[str, str]]:
    """[{key, label, status, detail}] with status done | active | pending | skipped."""
    state = (investigation or {}).get("state") or {}
    inv_status = (investigation or {}).get("status")
    at = _PHASE_AT.get(state.get("phase") or "", -1) if investigation else -1
    ended = inv_status in ("completed", "failed")
    s: dict[str, tuple[str, str]] = {"detect": ("done", f"by {incident.get('source') or 'a person'}")}

    if at < 0:
        s["investigate"] = ("pending", "not started")
    elif state.get("phase") == "awaiting_telemetry":
        s["investigate"] = ("active", "gathering fresh telemetry")
    elif at == 0 or (at == 1 and not state.get("evidence")):
        s["investigate"] = ("active", "collecting evidence")
    else:
        s["investigate"] = ("done", f"{len(state.get('evidence') or [])} evidence items")

    hyps = state.get("hypotheses") or []
    if at < 1 or not hyps:
        s["hypothesize"] = ("pending", "")
    elif at == 1 and not ended:
        s["hypothesize"] = ("active", f"{len(hyps)} explanations competing")
    else:
        out = sum(1 for h in hyps if h.get("status") in ("rejected", "weakened"))
        s["hypothesize"] = ("done", f"{len(hyps)} considered, {out} ruled out or weakened")

    exps = state.get("experiments") or []
    ran = [e for e in exps if e.get("baseline_uph") is not None]
    if any(e.get("status") in ("queued", "running") for e in exps) or state.get("phase") == "awaiting_simulation":
        s["simulate"] = ("active", "digital-twin test running")
    elif ran:
        s["simulate"] = ("done", f"{len(ran)} twin test{'s' if len(ran) > 1 else ''}")
    elif at >= 3 or ended:
        s["simulate"] = ("skipped", "twin test did not complete" if exps else "no test needed")
    else:
        s["simulate"] = ("pending", "")

    rstat = (rec or {}).get("status")
    if rec:
        s["recommend"] = ("done", {"withheld": "withheld: manual check first",
                                   "no_action": "no fault: no action"}.get(rstat or "", "recommended"))
    elif at == 3 and not ended:
        s["recommend"] = ("active", "deciding")
    elif ended:
        s["recommend"] = ("skipped", "no recommendation")
    else:
        s["recommend"] = ("pending", "")

    if rstat == "proposed":
        s["approve"] = ("active", "awaiting a manager")
    elif rstat in ("approved", "modified", "executed"):
        s["approve"] = ("done", f"approved by {rec.get('decided_by_role') or 'a manager'}")
    elif rstat in ("rejected", "expired", "no_action", "more_investigation"):
        s["approve"] = ("skipped", (rstat or "").replace("_", " "))
    else:
        s["approve"] = ("pending", "")

    outs = [a.get("outcome") for a in actions if a.get("outcome")]
    if any(o["status"] == "observing" for o in outs):
        s["verify"] = ("active", "measuring the result")
    elif outs:
        s["verify"] = ("done", outs[-1]["status"].replace("_", " "))
    else:
        s["verify"] = ("pending", "")
    # Monotonic: once a later stage has started, an earlier one is finished. The commander
    # goes back to planning after a twin test, and a raw read of that phase moved the track
    # BACKWARDS on screen (Hypothesize ✓ -> ●; LOOP UI run 2026-09-28).
    rows = [[k, label, s[k][0], s[k][1]] for k, label in STAGES]
    for i, row in enumerate(rows):
        if row[2] in ("active", "pending") and any(r[2] in ("done", "active") for r in rows[i + 1:]):
            row[2] = "done"
    return [{"key": k, "label": label, "status": st, "detail": d} for k, label, st, d in rows]
