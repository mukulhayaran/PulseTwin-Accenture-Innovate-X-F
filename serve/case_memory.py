"""Cross-plant operational memory: resolved incidents as reusable cases (ADR-067).

OPERATIONAL MEMORY, NOT MODEL RETRAINING. A case records what happened -- symptoms, the
hypotheses weighed, the root cause, what was proposed, what a human decided and why, and
what the action actually did -- so the next investigation can read it. Nothing here feeds
a model's training set; retraining stays a governed offline process (ADR-053, and
docs/MLOPS_LIFECYCLE.md).

Retrieval is HYBRID, as the brief asks, but without a vector database:
  1. structured filter  -- same incident kind (a throughput case never explains a defect);
  2. structured score   -- same zone, same station role (name), same plant;
  3. lexical similarity -- TF-IDF cosine over each case's descriptive text.
Embeddings would need a model call inside serve/ (the import wall) or a new store for a
few hundred rows; lexical TF-IDF is what ADR-046 already chose for the knowledge index.
Similarity is DESCRIPTIVE context only: it can nudge a hypothesis by at most 0.6 log-odds
(serve/incident_evidence.py) and it never enters a governor decision.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from serve import db

_WORD = re.compile(r"[a-z][a-z0-9_]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _role(name: str | None) -> str:
    return " ".join(_WORD.findall((name or "").lower()))


def signature(kind: str, zone: str | None, station_name: str | None) -> str:
    return f"{kind}|{zone or '-'}|{_role(station_name) or '-'}"


def context_text(incident: dict[str, Any], station: dict[str, Any],
                 root_cause_title: str | None, symptom_summaries: list[str]) -> str:
    return " ".join([incident["kind"], station.get("zone") or "", station.get("station_name") or "",
                     incident.get("title") or "", root_cause_title or "", *symptom_summaries])


def upsert_from_decision(ops: Any, incident: dict[str, Any], station: dict[str, Any],
                         state: dict[str, Any], recommendation: dict[str, Any]) -> str:
    """Written when the commander decides. One case per incident; later calls update it."""
    existing = db.query_one(ops, "SELECT case_id FROM incident_cases WHERE incident_id=?",
                            (incident["incident_id"],))
    sig = signature(incident["kind"], station.get("zone"), station.get("station_name"))
    a = state.get("assessment") or {}
    hyps = [{"key": h["key"], "title": h["title"], "confidence": h["confidence"],
             "status": h["status"]} for h in state.get("hypotheses", [])]
    options = [{"option_id": o["option_id"], "action_type": o["action_type"],
                "governor": o.get("governor", {}).get("decision")}
               for o in recommendation.get("options", [])]
    sims = [{"experiment_id": x.get("experiment_id"), "hypothesis": x.get("hypothesis"),
             "verdict": x.get("verdict"), "throughput_delta": x.get("throughput_delta")}
            for x in state.get("experiments", [])]
    summaries = [e["summary"] for e in state.get("evidence", [])[:8]]
    text = context_text(incident, station, a.get("root_cause_title"), summaries)
    root = a.get("root_cause_key") if a.get("assessment") == "CONFIDENT" else None
    if existing:
        db.execute(ops, """UPDATE incident_cases SET updated_at=?, hypotheses_considered=?,
                           root_cause=?, confidence=?, assessment=?, actions_considered=?,
                           simulation_prediction=?, context_text=? WHERE case_id=?""",
                   (_now(), json.dumps(hyps), root, a.get("confidence"), a.get("assessment"),
                    json.dumps(options), json.dumps(sims), text, existing["case_id"]))
        return existing["case_id"]
    prior = db.query_one(ops, "SELECT COUNT(*) AS n FROM incident_cases WHERE plant_id=? "
                              "AND problem_signature=?", (incident["plant_id"], sig))
    case_id = "CASE-" + secrets.token_hex(5)
    db.execute(ops, """INSERT INTO incident_cases
        (case_id, created_at, updated_at, plant_id, incident_id, problem_signature, kind,
         station_id, station_role, zone, sensor_maturity, symptoms, hypotheses_considered,
         root_cause, confidence, assessment, actions_considered, simulation_prediction,
         recurrence, context_text)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, _now(), _now(), incident["plant_id"], incident["incident_id"], sig,
         incident["kind"], incident.get("station_id"), station.get("station_name"),
         station.get("zone"),
         (state.get("sensor_maturity") or {}).get(str(incident.get("station_id"))),
         json.dumps(summaries), json.dumps(hyps), root, a.get("confidence"),
         a.get("assessment"), json.dumps(options), json.dumps(sims),
         int((prior or {}).get("n") or 0), text))
    return case_id


def record_decision(ops: Any, incident_id: str, decision: str, note: str | None,
                    selected_action: str | None) -> None:
    """A human decision -- including a REJECTION and its reason -- becomes memory."""
    db.execute(ops, "UPDATE incident_cases SET human_decision=?, decision_note=?, "
                    "selected_action=COALESCE(?, selected_action), updated_at=? "
                    "WHERE incident_id=?", (decision, note, selected_action, _now(), incident_id))


def record_outcome(ops: Any, incident_id: str, outcome: dict[str, Any],
                   recovery_time_s: float | None) -> None:
    db.execute(ops, "UPDATE incident_cases SET actual_outcome=?, prediction_error=?, "
                    "recovery_time_s=?, updated_at=? WHERE incident_id=?",
               (json.dumps({k: outcome.get(k) for k in (
                   "outcome_id", "status", "metric", "baseline_value", "actual_value",
                   "actual_change", "expected_change", "reason")}),
                outcome.get("prediction_error"), recovery_time_s, _now(), incident_id))


def _tfidf(docs: list[str]) -> list[dict[str, float]]:
    toks = [Counter(_WORD.findall(d.lower())) for d in docs]
    df = Counter(t for c in toks for t in c)
    n = len(docs)
    out = []
    for c in toks:
        v = {t: (1 + math.log(k)) * math.log((n + 1) / (df[t] + 0.5)) for t, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({t: x / norm for t, x in v.items()})
    return out


def similar(ops: Any, main_con: Any, incident: dict[str, Any], topo: list[dict[str, Any]],
            limit: int = 3) -> list[dict[str, Any]]:
    """Resolved cases most like this incident, across plants, newest evidence first."""
    rows = db.query_all(ops, "SELECT * FROM incident_cases WHERE kind=? AND incident_id<>? "
                             "ORDER BY created_at DESC LIMIT 200",
                        (incident["kind"], incident["incident_id"]))
    if not rows:
        return []
    here = next((t for t in topo if t["station_id"] == incident.get("station_id")), {})
    query = context_text(incident, here, None, [])
    vecs = _tfidf([query] + [r.get("context_text") or "" for r in rows])
    q = vecs[0]
    stations = {(r["plant_id"], r["station_id"]): r for r in db.query_all(
        main_con, "SELECT plant_id, station_id, station_name, buffer_capacity, sensor_tier "
                  "FROM station_config")}
    out = []
    for r, v in zip(rows, vecs[1:]):
        cos = sum(q.get(t, 0.0) * x for t, x in v.items())
        structured = (0.3 * (r.get("zone") == here.get("zone"))
                      + 0.2 * (_role(r.get("station_role")) == _role(here.get("station_name")))
                      + 0.1 * (r["plant_id"] == incident["plant_id"]))
        sim = round(min(1.0, structured + 0.4 * cos), 3)
        outcome = _loads(r.get("actual_outcome")) or {}
        diffs = []
        theirs = stations.get((r["plant_id"], r["station_id"]))
        if theirs and here and theirs.get("buffer_capacity") and here.get("buffer_capacity"):
            rel = here["buffer_capacity"] / theirs["buffer_capacity"] - 1
            if abs(rel) >= 0.1:
                diffs.append(f"buffer here is {abs(rel):.0%} {'smaller' if rel < 0 else 'larger'} "
                             f"than at the case's station ({here['buffer_capacity']} vs "
                             f"{theirs['buffer_capacity']})")
        if theirs and here and theirs.get("sensor_tier") != here.get("sensor_tier"):
            diffs.append(f"sensing differs: {here.get('sensor_tier')} here vs "
                         f"{theirs.get('sensor_tier')} there")
        if r["plant_id"] != incident["plant_id"]:
            diffs.append("different plant: re-test the prior intervention in THIS plant's twin "
                         "before recommending it")
        out.append({"case_id": r["case_id"], "plant_id": r["plant_id"],
                    "incident_id": r["incident_id"], "station_id": r["station_id"],
                    "station_label": f"S{r['station_id']} {r.get('station_role') or ''}".strip(),
                    "similarity": sim, "similarity_basis": {
                        "structured": round(structured, 3), "lexical_cosine": round(cos, 3)},
                    "root_cause": r.get("root_cause"), "assessment": r.get("assessment"),
                    "selected_action": r.get("selected_action"),
                    "human_decision": r.get("human_decision"),
                    "decision_note": r.get("decision_note"),
                    "outcome_status": outcome.get("status"),
                    "prediction_error": r.get("prediction_error"),
                    "important_differences": diffs})
    out.sort(key=lambda m: -m["similarity"])
    return [m for m in out if m["similarity"] >= 0.3][:limit]
