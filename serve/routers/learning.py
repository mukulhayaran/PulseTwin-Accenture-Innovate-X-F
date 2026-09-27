"""Operational memory, read side (ADR-064).

GET /api/learning/outcomes   evaluated action outcomes + projection accuracy
GET /api/learning/similar    prior incidents that match on STRUCTURED fields

Similarity is exact structured filtering first -- plant, then station, incident type and
intervention -- ranked by how many of those fields match. There is no semantic retrieval
here; if one is ever added it must narrow this set, never replace it. Every number these
endpoints return was computed by serve/outcomes.py, so an agent citing them is citing a
deterministic evaluation, not an earlier model's opinion.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from serve import db
from serve.middleware.auth import require_role

router = APIRouter()
FAILED = ("harmful", "no_material_effect")
WORKED = ("successful", "partially_successful")
EVALUATED = WORKED + FAILED


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How far observed changes landed from simulated projections, where both exist."""
    errors = [float(r["prediction_error"]) for r in rows if r.get("prediction_error") is not None]
    counts = Counter(r["status"] for r in rows)
    return {
        "outcomes": len(rows),
        "by_status": dict(counts),
        "evaluated": sum(counts[s] for s in EVALUATED),
        "success_rate": (round(sum(counts[s] for s in WORKED)
                               / max(1, sum(counts[s] for s in EVALUATED)), 3)
                         if any(counts[s] for s in EVALUATED) else None),
        "with_projection": len(errors),
        "mean_abs_projection_error": round(sum(abs(e) for e in errors) / len(errors), 4)
        if errors else None,
        "mean_projection_error": round(sum(errors) / len(errors), 4) if errors else None,
        "projection_error_means": "observed change minus projected change, in the metric's "
                                  "own unit; positive = the action did less good than projected",
    }


@router.get("/api/learning/outcomes")
def list_outcomes(request: Request, plant_id: str = "plant_a",
                  station_id: int | None = None,
                  intervention_type: str | None = Query(default=None, max_length=40),
                  limit: int = 20,
                  role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    where, params = "plant_id = ?", [plant_id]
    if station_id is not None:
        where += " AND station_id = ?"
        params.append(station_id)
    if intervention_type:
        where += " AND intervention_type = ?"
        params.append(intervention_type)
    rows = db.query_all(request.app.state.ops,
                        f"""SELECT outcome_id, created_at, station_id, proposal_id,
                                   experiment_id, detector, intervention_type, status, metric,
                                   direction, applied_at, window_s, baseline_value,
                                   expected_change, expected_ci_low, expected_ci_high,
                                   expected_source, actual_value, actual_change,
                                   prediction_error, n_events_before, n_events_after, reason,
                                   evaluated_at
                              FROM action_outcomes WHERE {where}
                             ORDER BY created_at DESC LIMIT ?""", (*params, limit))
    return {"plant_id": plant_id, "outcomes": rows, "count": len(rows),
            "accuracy": accuracy(rows),
            "method": "deterministic: configs/alert_policies.yaml outcome_evaluation"}


@router.get("/api/learning/similar")
def similar_incidents(request: Request, plant_id: str = "plant_a",
                      incident_type: str | None = Query(default=None, max_length=40),
                      station_id: int | None = None,
                      intervention_type: str | None = Query(default=None, max_length=40),
                      limit: int = 10,
                      role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    limit = max(1, min(int(limit), 30))
    rows = db.query_all(request.app.state.ops,
                        """SELECT record_id, created_at, station_id, incident_type,
                                  triage_run_id, proposal_id, decision, decision_note,
                                  intervention_type, experiment_id, outcome_id, expected,
                                  baseline, actual, prediction_error, success_status,
                                  confidence, simulator_version, model_version
                             FROM incident_learning_records WHERE plant_id = ?
                            ORDER BY created_at DESC LIMIT 500""", (plant_id,))
    scored = []
    for r in rows:
        match = []
        if incident_type and r["incident_type"] == incident_type:
            match.append("incident_type")
        if station_id is not None and r["station_id"] == station_id:
            match.append("station")
        if intervention_type and r["intervention_type"] == intervention_type:
            match.append("intervention")
        asked = sum(x is not None for x in (incident_type, station_id, intervention_type))
        if asked and not match:
            continue                      # structured filter: nothing in common, excluded
        for k in ("expected", "baseline", "actual"):
            r[k] = _loads(r[k])
        scored.append({**r, "matched_on": match, "match_score": len(match)})
    scored.sort(key=lambda r: str(r["created_at"]), reverse=True)   # newest first...
    scored.sort(key=lambda r: -r["match_score"])                      # ...within each level
    top = scored[:limit]

    by_intervention: dict[str, Counter] = defaultdict(Counter)
    for r in scored:
        if r["decision"] == "approved" and r["intervention_type"]:
            by_intervention[r["intervention_type"]][r["success_status"] or "pending"] += 1
    tallies = {k: dict(v) for k, v in by_intervention.items()}
    repeatedly_failed = sorted(k for k, v in by_intervention.items()
                               if sum(v[s] for s in FAILED) >= 2
                               and sum(v[s] for s in FAILED) > sum(v[s] for s in WORKED))
    return {"plant_id": plant_id,
            "query": {"incident_type": incident_type, "station_id": station_id,
                      "intervention_type": intervention_type},
            "records": top, "count": len(top), "matched_total": len(scored),
            "intervention_tally": tallies, "repeatedly_failed": repeatedly_failed,
            "rejected_recommendations": sum(1 for r in scored if r["decision"] == "rejected"),
            "similarity": "exact structured match on incident type, station and "
                          "intervention; ranked by fields matched, then recency"}
