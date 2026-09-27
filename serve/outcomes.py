"""Outcome monitoring and structured operational memory (ADR-064).

After a human approves an action and it is applied to the simulated twin:

1. open a monitoring window, recording the pre-action BASELINE of the detector's metric;
2. observe the configured post-action window of the same run;
3. compute the actual change;
4. compare it with what a linked simulation experiment projected, when one projected the
   same quantity;
5. classify it against configs/alert_policies.yaml `outcome_evaluation:`.

Every step is deterministic. No model decides whether an action worked; an agent may only
read and explain a verdict this module wrote. `incident_learning_records` is the join a
later investigation reads (`/api/learning/*`), and it is written by code on a human
decision and on an evaluation -- never from conversation text.

Windows are bounded by the run's event SEQUENCE, not by comparing timestamps across
backends (Postgres stores `ts` as TIMESTAMPTZ, SQLite as text); simulated time comes from
each event's own payload.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from serve import db
from serve.policy import outcome_policy

POLICY = outcome_policy()
METRIC_FOR = {"starvation": "stalled_share", "paint_env": "humidity_excess_pct",
              "tool_wear": "signal_deviation_pct"}
STALLED = ("starved", "blocked", "down")
SIGNALS = ("torque_nm", "weld_current_a")
TERMINAL = ("successful", "partially_successful", "no_material_effect", "harmful",
            "inconclusive", "cancelled")
# Which experiment scenarios test the SAME change as an approved catalogue action -- the
# only case where a projection and an observation can be compared.
EXPERIMENT_MATCH: dict[str, tuple[str, ...]] = {
    "increase_feed_buffer": ("mitigation:increase_feed_buffer", "feed_buffer_delta"),
    "rebalance_cycle": ("mitigation:rebalance_cycle", "cycle_time_pct"),
    "maintenance_ticket": ("mitigation:maintenance_ticket", "maintenance"),
    "shift_weld_load": ("mitigation:shift_weld_load",),
    "hvac_setpoint": ("mitigation:hvac_setpoint",),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


# --------------------------------------------------------------------------- measure
def measure(events: list[dict[str, Any]], metric: str,
            baselines: dict[str, float] | None = None) -> tuple[float | None, int]:
    """(value, events used) for one window of one station's events."""
    if metric == "stalled_share":
        n = len(events)
        if not n:
            return None, 0
        return sum(1 for e in events if e.get("station_state") in STALLED) / n, n
    if metric == "humidity_excess_pct":
        vals = [float(e["humidity_pct"]) - float(e["ambient_humidity_pct"]) for e in events
                if e.get("humidity_pct") is not None
                and e.get("ambient_humidity_pct") is not None]
        return (float(np.mean(vals)) if vals else None), len(vals)
    if metric == "signal_deviation_pct":
        vals = []
        for e in events:
            for sig in SIGNALS:
                base = (baselines or {}).get(f"{e.get('station_id')}|{sig}|{e.get('variant')}")
                if e.get(sig) is not None and base:
                    vals.append(abs(float(e[sig]) / float(base) - 1.0) * 100.0)
        return (float(np.mean(vals)) if vals else None), len(vals)
    raise ValueError(f"unknown outcome metric '{metric}'")


def classify(metric: str, baseline: float | None, actual: float | None,
             n_before: int, n_after: int) -> tuple[str, str, float | None]:
    """(status, reason, relative improvement). Lower is better for every metric here."""
    t = POLICY["metrics"][metric]
    need = POLICY["min_events"]
    if n_before < need or n_after < need or baseline is None or actual is None:
        return ("inconclusive", f"only {n_before} events before and {n_after} after; "
                                f"{need} needed on each side", None)
    if baseline < t["min_baseline"]:
        return ("inconclusive", f"baseline {baseline:.3f} is below {t['min_baseline']}: "
                                "there was nothing material to fix", None)
    rel = (baseline - actual) / baseline
    if rel >= t["success_rel"]:
        status = "successful"
    elif rel >= t["partial_rel"]:
        status = "partially_successful"
    elif rel <= -t["harm_rel"]:
        status = "harmful"
    else:
        status = "no_material_effect"
    return status, (f"{metric} {baseline:.3f} -> {actual:.3f} "
                    f"({rel:+.0%} relative improvement)"), rel


def confidence(status: str, n_before: int, n_after: int) -> str:
    """How much a later reader should lean on this verdict, from sample size alone."""
    if status in ("inconclusive", "cancelled"):
        return "low"
    need = POLICY["min_events"]
    return "high" if min(n_before, n_after) >= 3 * need else "medium"


def _events(ops: Any, run_id: str, station_id: int, seq_lo: int, seq_hi: int,
            newest_first_limit: int | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT payload FROM simulation_events WHERE run_id=? AND station_id=? "
           "AND seq>? AND seq<=? ORDER BY seq")
    if newest_first_limit:
        sql = sql.replace("ORDER BY seq", "ORDER BY seq DESC LIMIT ?")
        rows = db.query_all(ops, sql, (run_id, station_id, seq_lo, seq_hi,
                                       newest_first_limit))
    else:
        rows = db.query_all(ops, sql, (run_id, station_id, seq_lo, seq_hi))
    return [_loads(r["payload"]) for r in rows]


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)


# -------------------------------------------------------------------------- expected
def expected_for(ops: Any, proposal: dict[str, Any], action_type: str,
                 metric: str) -> dict[str, Any] | None:
    """What a linked experiment projected for THIS metric under THIS action, or None.

    Only `stalled_share` has a simulated analogue (the station's starved + blocked + down
    share of the horizon). The interval is a paired bootstrap over the experiment's own
    per-seed rows -- not a sum of per-metric intervals."""
    if metric != "stalled_share":
        return None
    exps = db.query_all(
        ops, "SELECT experiment_id, simulator_version FROM simulation_experiments "
             "WHERE status='done' AND (origin_proposal_id=? OR (origin_run_id IS NOT NULL "
             "AND origin_run_id=?)) ORDER BY created_at DESC",
        (proposal["proposal_id"], proposal.get("triage_run_id") or ""))
    wanted = EXPERIMENT_MATCH.get(action_type, ())
    for exp in exps:
        scenarios = db.query_all(ops, "SELECT * FROM simulation_scenarios WHERE "
                                      "experiment_id=? ORDER BY ordinal", (exp["experiment_id"],))
        base = next((s for s in scenarios if s["is_baseline"]), None)
        for s in scenarios:
            key = s["intervention_type"] or ""
            if key == "mitigation":
                key = f"mitigation:{(_loads(s['params']) or {}).get('action_type')}"
            if base is None or not s["supported"] or key not in wanted:
                continue
            rows = {r["seed"]: r for r in db.query_all(
                ops, "SELECT * FROM simulation_scenario_results WHERE scenario_id IN (?,?) "
                     "AND status='ok'", (s["scenario_id"], base["scenario_id"]))
                    if r["scenario_id"] == s["scenario_id"]}
            base_rows = {r["seed"]: r for r in db.query_all(
                ops, "SELECT * FROM simulation_scenario_results WHERE scenario_id=? "
                     "AND status='ok'", (base["scenario_id"],))}
            seeds = sorted(set(rows) & set(base_rows))
            if not seeds:
                continue

            def stalled(r: dict[str, Any]) -> float:
                return (float(r["starved_pct"]) + float(r["blocked_pct"])
                        + float(r["down_pct"])) / 100.0
            diffs = np.array([stalled(rows[k]) - stalled(base_rows[k]) for k in seeds])
            rng = np.random.default_rng(0)
            means = rng.choice(diffs, size=(2000, len(diffs)), replace=True).mean(axis=1)
            return {"expected_change": round(float(diffs.mean()), 4),
                    "ci_low": round(float(np.percentile(means, 2.5)), 4),
                    "ci_high": round(float(np.percentile(means, 97.5)), 4),
                    "seeds": len(seeds), "experiment_id": exp["experiment_id"],
                    "scenario": s["label"], "simulator_version": exp["simulator_version"],
                    "source": f"experiment {exp['experiment_id']}, scenario {s['label']}"}
    return None


# ------------------------------------------------------------------- learning records
def record_decision(ops: Any, proposal: dict[str, Any], *, decision: str, role: str,
                    note: str | None, action: dict[str, Any] | None,
                    model_version: str | None) -> str:
    """One learning record per human decision. A REJECTED recommendation is kept for
    audit and marked `not_applied` -- it is not a failed intervention."""
    record_id = "LRN-" + secrets.token_hex(6)
    evidence = _loads(proposal.get("trigger_evidence")) or {}
    exp = db.query_one(
        ops, "SELECT experiment_id, simulator_version, summary FROM simulation_experiments "
             "WHERE status='done' AND (origin_proposal_id=? OR (origin_run_id IS NOT NULL "
             "AND origin_run_id=?)) ORDER BY created_at DESC LIMIT 1",
        (proposal["proposal_id"], proposal.get("triage_run_id") or ""))
    summary = _loads((exp or {}).get("summary")) or {}
    db.execute(
        ops,
        """INSERT INTO incident_learning_records
           (record_id, created_at, updated_at, plant_id, station_id, incident_type,
            trigger_ref, triage_run_id, proposal_id, decision, decided_by_role,
            decision_note, intervention_type, action, experiment_id, expected,
            success_status, confidence, context, simulator_version, model_version)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (record_id, _now(), _now(), proposal["plant_id"], proposal["station_id"],
         proposal["detector"],
         json.dumps({"sim_run_id": proposal.get("sim_run_id"),
                     "fired_at": proposal.get("fired_at")}),
         proposal.get("triage_run_id"), proposal["proposal_id"], decision, role, note,
         (action or {}).get("action_type"), json.dumps(action) if action else None,
         (exp or {}).get("experiment_id"),
         json.dumps({"verdict": summary.get("verdict"), "best": summary.get("best_label")})
         if exp else None,
         "not_applied" if decision == "rejected" else "pending",
         None, json.dumps({"evidence": evidence}),
         (exp or {}).get("simulator_version"), model_version))
    return record_id


def _update_record(ops: Any, proposal_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(ops, f"UPDATE incident_learning_records SET {sets}, updated_at=? "
                    "WHERE proposal_id=? AND decision='approved'",
               (*[json.dumps(v) if isinstance(v, (dict, list)) else v
                  for v in fields.values()], _now(), proposal_id))


# ------------------------------------------------------------------- outcome windows
def open_outcome(ops: Any, proposal: dict[str, Any], action_type: str, *, run_id: str,
                 applied_ts: str, applied_seq: int,
                 baselines: dict[str, float] | None = None) -> str:
    """Start monitoring an action that was just applied to the simulated twin."""
    metric = METRIC_FOR[proposal["detector"]]
    station = int(proposal["station_id"])
    start = _ts(applied_ts) - timedelta(seconds=POLICY["baseline_window_s"])
    before = [e for e in _events(ops, run_id, station, 0, applied_seq, 5000)
              if _ts(e["ts"]) >= start]
    baseline, n_before = measure(before, metric, baselines)
    expected = expected_for(ops, proposal, action_type, metric)
    outcome_id = "OUT-" + secrets.token_hex(6)
    ends = (_ts(applied_ts) + timedelta(seconds=POLICY["window_s"])).isoformat()
    db.execute(
        ops,
        """INSERT INTO action_outcomes
           (outcome_id, created_at, updated_at, plant_id, station_id, proposal_id,
            sim_run_id, experiment_id, detector, intervention_type, status, metric,
            direction, applied_at, applied_seq, window_s, window_ends_at, baseline_value,
            expected_change, expected_ci_low, expected_ci_high, expected_source,
            n_events_before, thresholds, versions)
           VALUES (?,?,?,?,?,?,?,?,?,?,'observing',?,'lower_is_better',?,?,?,?,?,?,?,?,?,?,?,?)""",
        (outcome_id, _now(), _now(), proposal["plant_id"], station, proposal["proposal_id"],
         run_id, (expected or {}).get("experiment_id"), proposal["detector"], action_type,
         metric, applied_ts, applied_seq, POLICY["window_s"], ends, baseline,
         (expected or {}).get("expected_change"), (expected or {}).get("ci_low"),
         (expected or {}).get("ci_high"),
         (expected or {}).get("source") or f"no simulated analogue for {metric}",
         n_before, json.dumps(POLICY["metrics"][metric] | {"min_events": POLICY["min_events"]}),
         json.dumps({"simulator": (expected or {}).get("simulator_version")})))
    _update_record(ops, proposal["proposal_id"], outcome_id=outcome_id,
                   success_status="observing",
                   baseline={"metric": metric, "value": baseline, "events": n_before},
                   expected=expected or {"metric": metric, "expected_change": None,
                                         "reason": f"no simulated analogue for {metric}"})
    return outcome_id


def due(ops: Any, run_id: str, event_ts: str) -> list[dict[str, Any]]:
    """Observing windows of this run whose simulated end has passed."""
    rows = db.query_all(ops, "SELECT * FROM action_outcomes WHERE sim_run_id=? AND "
                             "status='observing'", (run_id,))
    now = _ts(event_ts)
    return [r for r in rows if _ts(r["window_ends_at"]) <= now]


def evaluate(ops: Any, outcome: dict[str, Any], current_seq: int,
             baselines: dict[str, float] | None = None) -> dict[str, Any]:
    """Close a window: measure after, classify, compare with the projection."""
    metric = outcome["metric"]
    after = _events(ops, outcome["sim_run_id"], int(outcome["station_id"]),
                    int(outcome["applied_seq"]), current_seq)
    actual, n_after = measure(after, metric, baselines)
    baseline = outcome["baseline_value"]
    status, reason, rel = classify(metric, baseline, actual,
                                   int(outcome["n_events_before"] or 0), n_after)
    change = (actual - baseline) if (actual is not None and baseline is not None) else None
    expected = outcome["expected_change"]
    error = (round(change - expected, 4)
             if change is not None and expected is not None and status != "inconclusive"
             else None)
    db.execute(
        ops,
        """UPDATE action_outcomes SET status=?, actual_value=?, actual_change=?,
               prediction_error=?, n_events_after=?, reason=?, evaluated_at=?, updated_at=?,
               observed=? WHERE outcome_id=?""",
        (status, actual, change, error, n_after, reason, _now(), _now(),
         json.dumps({"relative_improvement": rel}), outcome["outcome_id"]))
    _update_record(ops, outcome["proposal_id"], success_status=status,
                   confidence=confidence(status, int(outcome["n_events_before"] or 0), n_after),
                   actual={"metric": metric, "value": actual, "change": change,
                           "events": n_after, "relative_improvement": rel},
                   prediction_error=error)
    return {"outcome_id": outcome["outcome_id"], "status": status, "reason": reason}


def cancel_open(ops: Any, run_id: str, why: str) -> int:
    """A run that ends before a window matures leaves nothing to evaluate."""
    rows = db.query_all(ops, "SELECT outcome_id, proposal_id FROM action_outcomes WHERE "
                             "sim_run_id=? AND status='observing'", (run_id,))
    for r in rows:
        db.execute(ops, "UPDATE action_outcomes SET status='cancelled', reason=?, "
                        "evaluated_at=?, updated_at=? WHERE outcome_id=?",
                   (why, _now(), _now(), r["outcome_id"]))
        _update_record(ops, r["proposal_id"], success_status="cancelled", confidence="low")
    return len(rows)
