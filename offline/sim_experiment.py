"""Dynamic simulation experiments on an ISOLATED copy of the physical twin (ADR-064).

An experiment is a paired baseline plus up to N interventions (serve/experiment_catalog.py),
every one run on the SAME seed list. The twin is warmed once, the optional incident context
is injected and allowed to bite, and that snapshot is then FORKED (`copy.deepcopy`, every
RNG reseeded in place) per scenario per seed. Seed k is identical across scenarios, so the
intervention-minus-baseline difference is paired: its interval says how much of a gap is
the intervention and how much is the line's own noise.

Nothing here touches the live simulation. The live run (`serve/live_simulation.py`) replays
the committed stream in another process; this builds its own engine from config and seed,
and every fork is a deep copy of that private engine. `tests/test_sim_experiment.py` pins
that a fork leaves its source snapshot bit-identical.

The LLM never computes any of this. An agent picks interventions from the catalogue and
reads the numbers back; the deterministic simulator produces them.

Spawned detached by `serve/routers/experiments.py`, which has already written the
experiment and scenario rows. The worker reads its spec from the database (one source of
truth), writes one result row per seed as it goes, honours cancellation between seeds,
and heartbeats so a dead worker is detectable.

    python -m offline.sim_experiment --experiment-id E
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pickle

import numpy as np
import yaml

from offline.counterfactual import MAX_CONGESTION, _mitigate, _reseed, hypothesis
from offline.live_twin import warm_cache
from offline.live_twin.engine import LiveTwinEngine
from offline.live_twin.incidents import Incident
from serve import db
from serve.experiment_catalog import LIMITS, OBJECTIVES
from serve.live_simulation import MITIGATIONS

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_S = 300.0
BOOTSTRAP = 2000
SNAPSHOT_SEED = 42           # the warm-up is shared by every scenario; seeds vary the future
DEFAULT_ONSET_S = 1800.0     # time an injected condition is given to bite before the fork
STATES = ("idle", "working", "blocked", "starved", "down")
METRICS = ("throughput_uph", "wip_mean", "blocked_pct", "starved_pct", "down_pct",
           "downtime_s", "expected_defects", "alerts")
# Which direction is better, per metric -- used for the deterministic verdict only.
HIGHER_IS_BETTER = {"throughput_uph": True, "wip_mean": False, "blocked_pct": False,
                    "starved_pct": False, "down_pct": False, "downtime_s": False,
                    "expected_defects": False, "alerts": False}


def simulator_version() -> str:
    """What produced these numbers: a content hash of the simulator and twin sources, so a
    result can be traced to the exact physics that computed it."""
    h = hashlib.sha256()
    for rel in ("offline/simulator/line_model.py", "offline/live_twin/engine.py",
                "offline/live_twin/incidents.py", "offline/sim_experiment.py"):
        h.update((ROOT / rel).read_bytes())
    return f"live_twin@{h.hexdigest()[:12]}"


# ------------------------------------------------------------------------- the twin
def prepare(plant_id: str, station_id: int, incident: dict[str, Any] | None,
            budget_threshold: float) -> tuple[LiveTwinEngine, dict[str, Any] | None]:
    """The decision-point snapshot: warmed, the incident context injected and given
    DEFAULT_ONSET_S to bite. Returns the engine and the RESOLVED context (a watch
    detector's evidence resolved to a physical scenario and severity)."""
    # The ~13 s warm-up is identical for every experiment on this plant, threshold and
    # code, so it comes from a content-keyed disk cache (offline/live_twin/warm_cache.py).
    twin = warm_cache.warm_twin(plant_id, budget_threshold)
    if station_id not in {s.id for s in twin.cfg.stations}:
        raise ValueError(f"station {station_id} is not in {plant_id}")
    resolved = None
    if incident:
        if incident.get("detector"):
            scenario, severity, basis = hypothesis(incident["detector"],
                                                   incident.get("evidence") or {})
        else:
            scenario = incident["scenario"]
            severity = float(incident.get("severity", 0.5))
            basis = "severity given by the requester"
        if scenario == "buffer_congestion":
            severity = min(severity, MAX_CONGESTION)
        where = int(incident.get("station_id") or station_id)
        twin.inject(Incident(scenario, severity,
                             station_id=None if scenario == "high_humidity" else where))
        twin.advance(float(incident.get("onset_s") or DEFAULT_ONSET_S))
        resolved = {"scenario": scenario, "severity": round(severity, 3), "basis": basis,
                    "station_id": where}
    else:
        twin.advance(600.0)
    return twin, resolved


def _station(engine: LiveTwinEngine, station_id: int):
    return next(rt for rt in engine.sim.stations if rt.cfg.id == station_id)


def _state_totals(engine: LiveTwinEngine) -> dict[int, np.ndarray]:
    """Seconds per state per station so far, INCLUDING the interval still open: the
    simulator only accumulates on a transition, so a station starved for the whole
    window would otherwise read zero."""
    now = engine.now
    out = {}
    for rt in engine.sim.stations:
        totals = engine.sim.state_seconds[rt.cfg.id].sum(axis=0).copy()
        totals[STATES.index(rt.state)] += max(0.0, now - rt.state_since)
        out[rt.cfg.id] = totals
    return out


def _wip(engine: LiveTwinEngine) -> int:
    return sum(len(rt.out_buf) + (rt.unit is not None) + (rt.held is not None)
               for rt in engine.sim.stations)


def check_supported(item: dict[str, Any], station_id: int,
                    context: dict[str, Any] | None, engine: LiveTwinEngine) -> str | None:
    """Why this intervention cannot be simulated HERE, or None. Catalogue bounds were
    already checked by the API; this is what only the prepared twin knows."""
    kind = item["type"]
    if kind == "feed_buffer_delta" and _station(engine, station_id).index == 0:
        return "the first station has no upstream feed buffer"
    if kind == "mitigation":
        action = item["params"]["action_type"]
        scenario = (context or {}).get("scenario")
        if scenario is None or scenario not in MITIGATIONS[action]["scenarios"]:
            return (f"{action} addresses {list(MITIGATIONS[action]['scenarios'])}; this "
                    f"experiment's condition is {scenario or 'none'}")
    return None


def _apply(engine: LiveTwinEngine, item: dict[str, Any], station_id: int,
           context: dict[str, Any] | None) -> None:
    """Change exactly the parameter the catalogue names -- nothing else."""
    kind, params = item["type"], item["params"]
    rt = _station(engine, station_id)
    now = engine.now
    if kind == "feed_buffer_delta":
        up = engine.sim.stations[rt.index - 1]
        sid = up.cfg.id
        engine._base_capacity[sid] = max(1, engine._base_capacity[sid] + int(params["delta"]))
        up.capacity = engine.incidents.buffer_capacity(sid, engine._base_capacity[sid], now)
        if up.held is not None and up.state != "down":
            engine.sim._release_held(up, now)
    elif kind == "cycle_time_pct":
        rt.cfg.nominal_cycle_s *= 1.0 + float(params["pct"]) / 100.0
    elif kind == "repair_time_pct":
        rt.cfg.repair_time_s *= 1.0 + float(params["pct"]) / 100.0
    elif kind == "maintenance":
        for incident in engine.incidents.active(now):
            if (incident.scenario in ("torque_degradation", "station_breakdown")
                    and incident.station_id == station_id):
                incident.ended_at = now
        if rt.state != "down":
            engine.sim._on_failure(rt, now)   # the simulator's own repair path
    elif kind == "mitigation":
        _mitigate(engine, context["scenario"], params["action_type"], station_id)
    else:  # the API validated against the catalogue; reaching here is a bug in this file
        raise ValueError(f"unhandled intervention {kind}")


def run_replica(snapshot: LiveTwinEngine | bytes, seed: int, item: dict[str, Any] | None,
                station_id: int, context: dict[str, Any] | None,
                horizon_s: float) -> dict[str, Any]:
    """One seed of one scenario, on a private copy. `item` None is the baseline.

    `snapshot` may be the engine or its pickle: unpickling is the same isolated deep copy
    at about a sixth of `copy.deepcopy`'s cost (0.05 s vs 0.31 s, measured)."""
    t_wall = time.time()
    twin = pickle.loads(snapshot) if isinstance(snapshot, bytes) else copy.deepcopy(snapshot)
    _reseed(twin, seed)
    t0 = twin.now
    delay_s = (float(item["params"]["delay_min"]) * 60.0
               if item and item["type"] == "maintenance" else 0.0)
    applied = item is None
    if item is not None and delay_s <= 0:
        _apply(twin, item, station_id, context)
        applied = True
    u0, a0, s0 = twin.units_completed, twin.alerts_opened, _state_totals(twin)
    expected, wip = 0.0, []
    while twin.now - t0 < horizon_s - 1e-6:
        if not applied and twin.now - t0 >= delay_s:
            _apply(twin, item, station_id, context)
            applied = True
        step = min(CHECKPOINT_S, horizon_s - (twin.now - t0))
        if not applied:
            step = min(step, delay_s - (twin.now - t0))
        for event in twin.advance(step):
            if event["event_type"] == "inspection_outcome":
                expected += float(event["propensity"])
        wip.append(_wip(twin))
    s1 = _state_totals(twin)
    span = max(twin.now - t0, 1e-9)
    delta = {sid: s1[sid] - s0[sid] for sid in s1}
    active = {sid: (d[STATES.index("working")] + d[STATES.index("down")]) / span
              for sid, d in delta.items()}
    target = delta[station_id]
    return {
        "throughput_uph": float((twin.units_completed - u0) / (span / 3600.0)),
        "wip_mean": float(np.mean(wip)) if wip else 0.0,
        "blocked_pct": float(100.0 * target[STATES.index("blocked")] / span),
        "starved_pct": float(100.0 * target[STATES.index("starved")] / span),
        "down_pct": float(100.0 * target[STATES.index("down")] / span),
        "downtime_s": float(target[STATES.index("down")]),
        "expected_defects": float(expected),
        "alerts": int(twin.alerts_opened - a0),
        "bottleneck_station": int(max(active, key=active.get)),
        "runtime_s": time.time() - t_wall,
    }


# -------------------------------------------------------------------- parallel replicas
# Every replica is a pure function of (snapshot, seed, intervention): the result does not
# depend on which process runs it, so a pool changes wall-clock time and nothing else.
MAX_WORKERS = 3
_WORKER_BLOB: bytes | None = None


def _init_worker(blob: bytes) -> None:
    global _WORKER_BLOB
    _WORKER_BLOB = blob


def _replica_job(job: tuple) -> dict[str, Any]:
    _scenario_id, seed, item, station_id, context, horizon = job
    return _safe_replica(_WORKER_BLOB, seed, item, station_id, context, horizon)


def _safe_replica(blob: bytes, seed: int, item: dict[str, Any] | None, station_id: int,
                  context: dict[str, Any] | None, horizon: float) -> dict[str, Any]:
    try:
        m = run_replica(blob, seed, item, station_id, context, horizon)
        m.update(seed=seed, status="ok", error=None)
    except Exception as exc:  # noqa: BLE001 - a failed seed is recorded, not fatal
        m = {k: None for k in METRICS} | {
            "seed": seed, "status": "failed", "bottleneck_station": None,
            "runtime_s": None, "error": f"{type(exc).__name__}: {exc}"}
    return m


def _replicas(blob: bytes, jobs: list[tuple[str, int, dict[str, Any] | None]],
              station_id: int, context: dict[str, Any] | None, horizon: float):
    """Yield (job, metrics) in SUBMISSION order. A `fork` pool where the OS has one (the
    Linux container: workers inherit the snapshot, no re-import); serial elsewhere. The
    caller stops iterating to cancel -- the pool is then shut down, pending jobs dropped."""
    import multiprocessing as mp
    workers = min(MAX_WORKERS, os.cpu_count() or 1, len(jobs))
    if workers <= 1 or "fork" not in mp.get_all_start_methods():
        for job in jobs:
            yield job, _safe_replica(blob, job[1], job[2], station_id, context, horizon)
        return
    from concurrent.futures import ProcessPoolExecutor
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"),
                               initializer=_init_worker, initargs=(blob,))
    try:
        futures = [pool.submit(_replica_job, (*job, station_id, context, horizon))
                   for job in jobs]
        for job, fut in zip(jobs, futures):
            yield job, fut.result()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------------ statistics
def _interval(values: np.ndarray, rng: np.random.Generator) -> dict[str, float]:
    """Mean, a 95% percentile-bootstrap interval of the mean, and the empirical p10/p90
    of the seeds themselves. `provenance: inferred` travels with every interval so the
    verifier's contract-#1 cross-check applies to any claim that cites it."""
    if len(values) == 1:
        v = round(float(values[0]), 4)
        return {"mean": v, "ci_low": v, "ci_high": v, "p10": v, "p90": v, "n": 1,
                "provenance": "inferred"}
    means = rng.choice(values, size=(BOOTSTRAP, len(values)), replace=True).mean(axis=1)
    return {"mean": round(float(values.mean()), 4),
            "ci_low": round(float(np.percentile(means, 2.5)), 4),
            "ci_high": round(float(np.percentile(means, 97.5)), 4),
            "p10": round(float(np.percentile(values, 10)), 4),
            "p90": round(float(np.percentile(values, 90)), 4),
            "n": int(len(values)), "provenance": "inferred"}


def _downtime_value() -> tuple[float, str]:
    econ = yaml.safe_load((ROOT / "configs" / "economics.yaml").read_text(encoding="utf-8"))
    item = econ["costs"]["downtime_cost_per_hour"]
    return float(item["value"]), ("configs/economics.yaml costs.downtime_cost_per_hour "
                                  "divided by the paired baseline's throughput")


def summarise(rows_by_scenario: dict[str, list[dict[str, Any]]], baseline_id: str,
              labels: dict[str, str], objective: str, seed: int = 0) -> dict[str, Any]:
    """Per-scenario intervals plus paired differences against the baseline, and a
    DETERMINISTIC verdict on the objective. No model decides whether anything worked."""
    rng = np.random.default_rng(seed)
    metric = OBJECTIVES[objective]
    base_rows = {r["seed"]: r for r in rows_by_scenario.get(baseline_id, [])
                 if r["status"] == "ok"}
    usd_per_hour, usd_source = _downtime_value()
    scenarios: dict[str, Any] = {}
    for sid, rows in rows_by_scenario.items():
        ok = [r for r in rows if r["status"] == "ok"]
        entry: dict[str, Any] = {"label": labels[sid], "seeds_ok": len(ok),
                                 "seeds_failed": len(rows) - len(ok)}
        if ok:
            for m in METRICS:
                entry[m] = _interval(np.array([r[m] for r in ok], float), rng)
            modes = Counter(r["bottleneck_station"] for r in ok)
            entry["bottleneck_station"] = modes.most_common(1)[0][0]
            entry["bottleneck_share"] = round(modes.most_common(1)[0][1] / len(ok), 3)
        if sid != baseline_id and ok and base_rows:
            paired = [(r, base_rows[r["seed"]]) for r in ok if r["seed"] in base_rows]
            diffs = {m: np.array([a[m] - b[m] for a, b in paired], float) for m in METRICS}
            entry["vs_baseline"] = {m: _interval(diffs[m], rng) for m in METRICS}
            entry["vs_baseline"]["paired_seeds"] = len(paired)
            base_uph = float(np.mean([b["throughput_uph"] for _, b in paired]))
            if base_uph > 0:
                value = diffs["throughput_uph"] * usd_per_hour / base_uph
                entry["expected_value_usd_per_hour"] = {**_interval(value, rng),
                                                        "source": usd_source}
            entry["intervention_cost_usd"] = None
            entry["intervention_cost_reason"] = ("configs/economics.yaml carries no cost "
                                                 "for this intervention")
        scenarios[sid] = entry

    # The verdict: the best intervention on the objective, only if its paired interval
    # excludes zero in the better direction.
    better_up = HIGHER_IS_BETTER[metric]
    candidates = []
    for sid, e in scenarios.items():
        d = (e.get("vs_baseline") or {}).get(metric)
        if not d:
            continue
        gain = d["mean"] if better_up else -d["mean"]
        clear = d["ci_low"] > 0 if better_up else d["ci_high"] < 0
        candidates.append((gain, clear, sid))
    candidates.sort(reverse=True)
    if not candidates:
        verdict, best = "no_scenarios", None
    elif any(clear for _, clear, _ in candidates):
        gain, _, best = next(c for c in candidates if c[1])
        verdict = "improved"
    elif candidates[0][0] > 0:
        verdict, best = "too_uncertain", None
    else:
        verdict, best = "no_improvement", None
    return {"objective": objective, "objective_metric": metric, "verdict": verdict,
            "best_scenario_id": best, "best_label": labels.get(best) if best else None,
            "ranking": [labels[s] for _, _, s in candidates],
            "scenarios": scenarios}


# ---------------------------------------------------------------------------- worker
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def execute(con: Any, experiment_id: str, budget_threshold: float | None = None,
            clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Run an experiment whose rows the API already wrote. Returns the summary."""
    exp = db.query_one(con, "SELECT * FROM simulation_experiments WHERE experiment_id=?",
                       (experiment_id,))
    if exp is None:
        raise ValueError(f"unknown experiment {experiment_id}")
    if exp["status"] != "queued":   # cancelled (or reaped) before this process started
        return _loads(exp.get("summary")) or {"verdict": exp["status"], "best_label": None}
    started = clock()
    db.execute(con, "UPDATE simulation_experiments SET status='running', started_at=?, "
                    "heartbeat_at=?, updated_at=?, pid=?, simulator_version=? "
                    "WHERE experiment_id=?",
               (_now(), _now(), _now(), os.getpid(), simulator_version(), experiment_id))
    scenarios = db.query_all(con, "SELECT * FROM simulation_scenarios WHERE experiment_id=? "
                                  "ORDER BY ordinal", (experiment_id,))
    seeds = [int(s) for s in _loads(exp["seeds"])]
    horizon = float(exp["horizon_s"])
    station_id = int(exp["station_id"])
    # The API passes the plant's published threshold (the serving image carries no SQLite
    # artifact, ADR-052); the artifact file is only a fallback for offline/test runs.
    threshold = (budget_threshold if budget_threshold is not None
                 else _budget_threshold(exp["plant_id"]))
    snapshot, context = prepare(exp["plant_id"], station_id, _loads(exp["incident"]),
                                threshold)
    if context is not None:
        db.execute(con, "UPDATE simulation_experiments SET incident=? WHERE experiment_id=?",
                   (json.dumps({**(_loads(exp["incident"]) or {}), "resolved": context}),
                    experiment_id))

    rows_by: dict[str, list[dict[str, Any]]] = {}
    labels = {s["scenario_id"]: s["label"] for s in scenarios}
    baseline_id = next(s["scenario_id"] for s in scenarios if s["is_baseline"])
    status = "done"
    jobs: list[tuple[str, int, dict[str, Any] | None]] = []
    for sc in scenarios:
        if not sc["supported"]:
            continue
        item = None if sc["is_baseline"] else {"type": sc["intervention_type"],
                                                "params": _loads(sc["params"]) or {}}
        if item is not None:
            why = check_supported(item, station_id, context, snapshot)
            if why:
                db.execute(con, "UPDATE simulation_scenarios SET supported=0, reason=? "
                                "WHERE scenario_id=?", (why, sc["scenario_id"]))
                continue
        rows_by[sc["scenario_id"]] = []
        jobs.extend((sc["scenario_id"], seed, item) for seed in seeds)

    flags = db.query_one(con, "SELECT cancel_requested FROM simulation_experiments "
                              "WHERE experiment_id=?", (experiment_id,))
    if flags and int(flags["cancel_requested"] or 0):
        jobs, status = [], "cancelled"          # cancelled before any seed ran
    blob = pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
    for (scenario_id, seed, _item), m in _replicas(blob, jobs, station_id, context, horizon):
        rows_by[scenario_id].append(m)
        db.execute(con,
                   """INSERT INTO simulation_scenario_results
                      (created_at, experiment_id, scenario_id, seed, status,
                       throughput_uph, wip_mean, blocked_pct, starved_pct, down_pct,
                       downtime_s, expected_defects, alerts, bottleneck_station,
                       runtime_s, error)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (_now(), experiment_id, scenario_id, seed, m["status"],
                    m["throughput_uph"], m["wip_mean"], m["blocked_pct"],
                    m["starved_pct"], m["down_pct"], m["downtime_s"],
                    m["expected_defects"], m["alerts"], m["bottleneck_station"],
                    m["runtime_s"], m["error"]))
        db.execute(con, "UPDATE simulation_experiments SET heartbeat_at=?, updated_at=? "
                        "WHERE experiment_id=?", (_now(), _now(), experiment_id))
        # Cancellation and the wall clock are checked between results, as before.
        flags = db.query_one(con, "SELECT cancel_requested FROM simulation_experiments "
                                  "WHERE experiment_id=?", (experiment_id,))
        if flags and int(flags["cancel_requested"] or 0):
            status = "cancelled"
        elif clock() - started > LIMITS["wallclock_timeout_s"]:
            status = "timeout"
        if status != "done":
            break

    summary = summarise(rows_by, baseline_id, labels, exp["objective"])
    for sid, entry in summary["scenarios"].items():
        db.execute(con, "UPDATE simulation_scenarios SET summary=? WHERE scenario_id=?",
                   (json.dumps(entry), sid))
    summary["context"] = context
    db.execute(con, "UPDATE simulation_experiments SET status=?, finished_at=?, updated_at=?, "
                    "runtime_s=?, summary=?, error=? WHERE experiment_id=?",
               (status, _now(), _now(), round(clock() - started, 2), json.dumps(summary),
                None if status in ("done", "cancelled")
                else f"wall-clock limit {LIMITS['wallclock_timeout_s']:.0f}s reached",
                experiment_id))
    return summary


def _budget_threshold(plant_id: str) -> float:
    """The plant's published alert threshold, from the same artifact the API serves."""
    import sqlite3
    con = sqlite3.connect(f"file:{ROOT / 'artifacts' / 'pulsetwin.db'}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT budget_threshold FROM plant_meta WHERE plant_id=?",
                          (plant_id,)).fetchone()
    finally:
        con.close()
    return float(row[0]) if row else 0.5


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--budget-threshold", type=float, default=None)
    args = p.parse_args(argv)
    # Up to max_concurrent x MAX_WORKERS replica processes on the API's own CPUs: at normal
    # priority they starved the server and one request took >30 s mid-demo (LOOP run
    # 2026-09-28). Lower priority, inherited by the pool: the API always runs first and a
    # twin test only takes longer. POSIX only; a no-op elsewhere.
    if hasattr(os, "nice"):
        os.nice(10)
    con, _ = db.open_ops_only()
    try:
        summary = execute(con, args.experiment_id, args.budget_threshold)
    except Exception as exc:  # noqa: BLE001 - the row must never sit at `running`
        db.execute(con, "UPDATE simulation_experiments SET status='failed', error=?, "
                        "finished_at=?, updated_at=? WHERE experiment_id=?",
                   (f"{type(exc).__name__}: {exc}", _now(), _now(), args.experiment_id))
        raise
    print(f"[sim_experiment] {args.experiment_id} {summary['verdict']} "
          f"best={summary['best_label']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
