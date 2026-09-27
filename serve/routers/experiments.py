"""Dynamic simulation experiments (ADR-064).

POST /api/simulation-experiments                  create + spawn the isolated worker
GET  /api/simulation-experiments                  list (plant, origin run)
GET  /api/simulation-experiments/{id}             the experiment, per-scenario intervals
GET  /api/simulation-experiments/{id}/comparison  the compact intervention table
POST /api/simulation-experiments/{id}/cancel      stop between seeds

The computation lives in `offline/sim_experiment.py` (the physical twin cannot be imported
past the wall); this router validates against `serve/experiment_catalog.py`, enforces the
resource limits, writes the experiment and scenario rows BEFORE spawning -- so a run is
never invisible even if the worker dies -- and reads results back from the database, so
they survive an API restart.

A fresh experiment is labelled `method: "fresh_simulation"`. The precomputed what-if grid
(POST /api/whatif) is a different thing and says so (`mode: "interpolated"`).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from serve import db
from serve.experiment_catalog import (
    CONTEXT_SCENARIOS, INTERVENTIONS, LIMITS, OBJECTIVES, PROFILES, UNSUPPORTED, label,
    validate_intervention,
)
from serve.middleware.audit import log_action
from serve.middleware.auth import require_role

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ("queued", "running")
# A worker heartbeats after every seed (a 4 h seed is ~10 s of wall-clock) and after the
# ~12 s warm-up; this much silence means the process is gone.
STALE_S = 180.0
MAX_WAIT_S = 150.0
WATCH_DETECTORS = ("starvation", "paint_env", "tool_wear")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _jobs_dir() -> Path:
    import tempfile
    return Path(os.environ.get("PULSETWIN_JOBS_DIR")
                or Path(tempfile.gettempdir()) / "pulsetwin_triage_runs")


def _age_s(ts: Any) -> float:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def _abandoned(row: dict[str, Any]) -> bool:
    """Queued or running, with no heartbeat for STALE_S: the worker is gone."""
    if row["status"] not in ACTIVE:
        return False
    return _age_s(row.get("heartbeat_at") or row.get("created_at")) > STALE_S


def reap_abandoned(ops: Any) -> int:
    """Persist the verdict for every abandoned worker. Called on create and at startup,
    so a dead worker cannot hold a concurrency slot or show `running` forever."""
    rows = db.query_all(ops, "SELECT experiment_id, status, heartbeat_at, created_at "
                             "FROM simulation_experiments WHERE status IN ('queued','running')")
    dead = [r for r in rows if _abandoned(r)]
    for r in dead:
        db.execute(ops, "UPDATE simulation_experiments SET status='failed', error=?, "
                        "finished_at=?, updated_at=? WHERE experiment_id=?",
                   (f"worker_stopped: no heartbeat for {int(STALE_S)} s", _now(), _now(),
                    r["experiment_id"]))
    return len(dead)


class ExperimentRequest(BaseModel):
    plant_id: str = "plant_a"
    station_id: int
    objective: str = "throughput"
    interventions: list[dict[str, Any]] = Field(min_length=1)
    profile: Literal["smoke", "standard"] = "smoke"
    horizon_s: float | None = Field(default=None, gt=0)
    seeds: list[int] | None = None
    incident: dict[str, Any] | None = None
    origin_run_id: str | None = Field(default=None, max_length=64)
    origin_proposal_id: str | None = Field(default=None, max_length=64)
    wait_s: float = Field(default=0.0, ge=0.0, le=MAX_WAIT_S)


def _validate(req: ExperimentRequest, request: Request) -> tuple[list[int], float]:
    plants = request.app.state.plants
    if req.plant_id not in plants:
        raise HTTPException(404, f"unknown plant '{req.plant_id}'")
    stations = {r["station_id"] for r in db.query_all(
        request.app.state.main_con,
        "SELECT station_id FROM station_config WHERE plant_id=?", (req.plant_id,))}
    if req.station_id not in stations:
        raise HTTPException(422, f"station {req.station_id} is not in {req.plant_id}")
    if req.objective not in OBJECTIVES:
        raise HTTPException(422, f"objective must be one of {sorted(OBJECTIVES)}")
    if len(req.interventions) + 1 > LIMITS["max_scenarios"]:
        raise HTTPException(422, f"at most {int(LIMITS['max_scenarios']) - 1} interventions "
                                 "(plus the paired baseline) per experiment")
    for item in req.interventions:
        problem = validate_intervention(item)
        if problem and item.get("type") not in UNSUPPORTED:
            raise HTTPException(422, problem)
    profile = PROFILES[req.profile]
    horizon = float(req.horizon_s or profile["horizon_s"])
    if horizon > LIMITS["max_horizon_s"]:
        raise HTTPException(422, f"horizon_s must be <= {LIMITS['max_horizon_s']:.0f}")
    seeds = list(req.seeds) if req.seeds else list(range(1, int(profile["seeds"]) + 1))
    if not 1 <= len(seeds) <= LIMITS["max_seeds"] or len(set(seeds)) != len(seeds):
        raise HTTPException(422, f"seeds must be 1..{int(LIMITS['max_seeds'])} distinct ints")
    if any(not 0 <= s <= 1_000_000 for s in seeds):
        raise HTTPException(422, "each seed must be within [0, 1000000]")
    inc = req.incident
    if inc:
        if inc.get("detector"):
            if inc["detector"] not in WATCH_DETECTORS or not isinstance(
                    inc.get("evidence") or {}, dict):
                raise HTTPException(422, f"incident.detector must be one of {WATCH_DETECTORS}"
                                         " with an evidence object")
        elif inc.get("scenario") not in CONTEXT_SCENARIOS:
            raise HTTPException(422, f"incident.scenario must be one of {CONTEXT_SCENARIOS}")
        sev = inc.get("severity", 0.5)
        if isinstance(sev, bool) or not isinstance(sev, (int, float)) or not 0 < sev <= 1:
            raise HTTPException(422, "incident.severity must be within (0, 1]")
        if "station_id" in inc and inc["station_id"] not in stations:
            raise HTTPException(422, "incident.station_id is not in this plant")
    return seeds, horizon


def _enforce_budgets(ops: Any, req: ExperimentRequest, cost: int) -> None:
    """Server-side, so an agent cannot out-count its own instructions."""
    active = [r for r in db.query_all(
        ops, "SELECT status, heartbeat_at, created_at FROM simulation_experiments "
             "WHERE status IN ('queued','running')") if not _abandoned(r)]
    if len(active) >= LIMITS["max_concurrent"]:
        raise HTTPException(429, f"{len(active)} experiments already running; the limit is "
                                 f"{int(LIMITS['max_concurrent'])}")
    if req.origin_run_id:
        prior = db.query_all(ops, "SELECT seeds, interventions FROM simulation_experiments "
                                  "WHERE origin_run_id=?", (req.origin_run_id,))
        if len(prior) >= LIMITS["max_experiments_per_run"]:
            raise HTTPException(429, f"run {req.origin_run_id} already used its "
                                     f"{int(LIMITS['max_experiments_per_run'])} experiments")
        spent = sum(len(_loads(p["seeds"])) * (len(_loads(p["interventions"])) + 1)
                    for p in prior)
        if spent + cost > LIMITS["max_seeds_per_run"]:
            raise HTTPException(429, f"run {req.origin_run_id} would exceed its simulation "
                                     f"budget of {int(LIMITS['max_seeds_per_run'])} "
                                     f"scenario-seeds ({spent} spent, {cost} requested)")


@router.post("/api/simulation-experiments")
async def create_experiment(req: ExperimentRequest, request: Request,
                            role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """Async so `wait_s` never parks a worker thread (ADR-009): the handler only awaits
    between millisecond reads while the detached process simulates."""
    view = await asyncio.to_thread(_create, req, request, role)
    deadline = time.time() + req.wait_s
    while view["status"] in ACTIVE and time.time() < deadline:
        await asyncio.sleep(1.0)
        view = await asyncio.to_thread(_view, request.app.state.ops, view["experiment_id"])
    return view


def _create(req: ExperimentRequest, request: Request, role: str) -> dict[str, Any]:
    ops = request.app.state.ops
    seeds, horizon = _validate(req, request)
    reap_abandoned(ops)
    supported_items = [i for i in req.interventions if validate_intervention(i) is None]
    _enforce_budgets(ops, req, len(seeds) * (len(supported_items) + 1))

    experiment_id = "EXP-" + secrets.token_hex(6)
    now = _now()
    db.execute(ops,
               """INSERT INTO simulation_experiments
                  (experiment_id, created_at, updated_at, plant_id, station_id, objective,
                   origin_run_id, origin_proposal_id, requested_by, status, profile,
                   horizon_s, seeds, incident, interventions, cancel_requested, heartbeat_at)
                  VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,0,?)""",
               (experiment_id, now, now, req.plant_id, req.station_id, req.objective,
                req.origin_run_id, req.origin_proposal_id, role, req.profile, horizon,
                json.dumps(seeds), json.dumps(req.incident) if req.incident else None,
                json.dumps(req.interventions), now))
    rows = [(f"{experiment_id}-0", 0, "baseline (no intervention)", None, None, 1, 1,
             None, "the line as prepared, same seeds, nothing changed")]
    for i, item in enumerate(req.interventions, start=1):
        problem = validate_intervention(item)
        rows.append((f"{experiment_id}-{i}", i,
                     label(item) if problem is None else str(item.get("type")),
                     item.get("type"), json.dumps(item.get("params") or {}), 0,
                     0 if problem else 1, problem,
                     INTERVENTIONS[item["type"]]["simulated_as"] if problem is None else None))
    for r in rows:
        db.execute(ops,
                   """INSERT INTO simulation_scenarios
                      (scenario_id, experiment_id, ordinal, label, intervention_type, params,
                       is_baseline, supported, reason, simulated_as)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (r[0], experiment_id, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]))

    jobs = _jobs_dir()
    jobs.mkdir(parents=True, exist_ok=True)
    log = open(jobs / f"{experiment_id}.log", "w", encoding="utf-8")  # noqa: SIM115
    subprocess.Popen(
        [sys.executable, "-m", "offline.sim_experiment", "--experiment-id", experiment_id,
         "--budget-threshold",
         str(float(request.app.state.plants[req.plant_id]["budget_threshold"]))],
        cwd=str(ROOT),
        env={**os.environ, "OMP_NUM_THREADS": "1", "PYTHONPATH": str(ROOT)},
        stdout=log, stderr=subprocess.STDOUT,
    )
    log_action(request, ops, role, "simulation_experiment_started", "simulation_experiment",
               experiment_id, {"plant_id": req.plant_id, "station_id": req.station_id,
                               "interventions": [i.get("type") for i in req.interventions],
                               "seeds": len(seeds), "origin_run_id": req.origin_run_id})

    return _view(ops, experiment_id)


def _view(ops: Any, experiment_id: str) -> dict[str, Any]:
    exp = db.query_one(ops, "SELECT * FROM simulation_experiments WHERE experiment_id=?",
                       (experiment_id,))
    if exp is None:
        raise HTTPException(404, f"unknown experiment '{experiment_id}'")
    scenarios = db.query_all(ops, "SELECT * FROM simulation_scenarios WHERE experiment_id=? "
                                  "ORDER BY ordinal", (experiment_id,))
    done = db.query_one(ops, "SELECT COUNT(*) AS n, SUM(CASE WHEN status='failed' THEN 1 "
                             "ELSE 0 END) AS failed FROM simulation_scenario_results "
                             "WHERE experiment_id=?", (experiment_id,)) or {}
    seeds = _loads(exp["seeds"]) or []
    status = exp["status"]
    error = exp.get("error")
    if _abandoned(exp):
        status, error = "failed", f"worker_stopped: no heartbeat for {int(STALE_S)} s"
    summary = _loads(exp.get("summary")) or {}
    out_scenarios = []
    for s in scenarios:
        entry = _loads(s.get("summary")) or {}
        out_scenarios.append({
            "scenario_id": s["scenario_id"], "label": s["label"],
            "intervention_type": s["intervention_type"],
            "params": _loads(s["params"]) or {}, "is_baseline": bool(s["is_baseline"]),
            "supported": bool(s["supported"]), "reason": s["reason"],
            "simulated_as": s["simulated_as"],
            "seeds_ok": entry.get("seeds_ok", 0), "seeds_failed": entry.get("seeds_failed", 0),
            "metrics": {k: entry[k] for k in (
                "throughput_uph", "wip_mean", "blocked_pct", "starved_pct", "down_pct",
                "downtime_s", "expected_defects", "alerts") if k in entry},
            "bottleneck_station": entry.get("bottleneck_station"),
            "bottleneck_share": entry.get("bottleneck_share"),
            "vs_baseline": entry.get("vs_baseline"),
            "expected_value_usd_per_hour": entry.get("expected_value_usd_per_hour"),
            "intervention_cost_usd": entry.get("intervention_cost_usd"),
            "intervention_cost_reason": entry.get("intervention_cost_reason"),
        })
    runnable = sum(1 for s in scenarios if s["supported"])
    incident = _loads(exp.get("incident"))
    return {
        "experiment_id": experiment_id, "status": status, "error": error,
        "plant_id": exp["plant_id"], "station_id": exp["station_id"],
        "objective": exp["objective"], "profile": exp["profile"],
        "origin_run_id": exp["origin_run_id"], "origin_proposal_id": exp["origin_proposal_id"],
        "requested_by": exp["requested_by"], "created_at": exp["created_at"],
        "started_at": exp["started_at"], "finished_at": exp["finished_at"],
        "runtime_s": exp["runtime_s"], "simulator_version": exp["simulator_version"],
        "method": "fresh_simulation",
        "method_detail": ("isolated copies of the physical twin (offline.live_twin), a paired "
                          "baseline on identical seeds; intervals are 95% percentile "
                          "bootstrap of the seed mean, p10/p90 are the seeds themselves"),
        "horizon_s": exp["horizon_s"], "seeds": seeds, "seed_count": len(seeds),
        "incident": incident,
        "progress": {"results_done": int(done.get("n") or 0),
                     "results_failed": int(done.get("failed") or 0),
                     "results_total": runnable * len(seeds)},
        "verdict": summary.get("verdict"), "best_label": summary.get("best_label"),
        "ranking": summary.get("ranking", []),
        "scenarios": out_scenarios,
        "assumptions": [
            "Every metric is simulated (provenance: inferred), never measured.",
            "Station percentages are shares of the horizon at the requested station.",
            "Expected defects sum each inspected unit's propensity (conditional Monte Carlo).",
            "Catalogue mitigation magnitudes are modelled assumptions (docs/ASSUMPTIONS.md, "
            "Mitigation effects).",
        ],
    }


@router.get("/api/simulation-experiments/{experiment_id}")
async def get_experiment(experiment_id: str, request: Request,
                         wait_s: float = Query(default=0.0, ge=0.0, le=MAX_WAIT_S),
                         role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    """`wait_s` lets an agent read a running experiment in ONE tool call rather than a
    provider round trip per poll."""
    view = await asyncio.to_thread(_view, request.app.state.ops, experiment_id)
    deadline = time.time() + wait_s
    while view["status"] in ACTIVE and time.time() < deadline:
        await asyncio.sleep(1.0)
        view = await asyncio.to_thread(_view, request.app.state.ops, experiment_id)
    return view


def comparison(view: dict[str, Any]) -> dict[str, Any]:
    """The compact table: one row per scenario, deltas against the paired baseline."""
    rows = []
    for s in view["scenarios"]:
        d = s.get("vs_baseline") or {}
        m = s.get("metrics") or {}

        def pick(key: str) -> dict[str, Any] | None:
            return (m.get(key) if s["is_baseline"] else d.get(key)) or None

        rows.append({
            "label": s["label"], "is_baseline": s["is_baseline"],
            "supported": s["supported"], "reason": s["reason"],
            "throughput_uph": pick("throughput_uph"), "starved_pct": pick("starved_pct"),
            "blocked_pct": pick("blocked_pct"), "wip_mean": pick("wip_mean"),
            "expected_defects": pick("expected_defects"),
            "bottleneck_station": s["bottleneck_station"],
            "seeds_ok": s["seeds_ok"],
            "distinguishable": bool(d and d.get("throughput_uph") and (
                d["throughput_uph"]["ci_low"] > 0 or d["throughput_uph"]["ci_high"] < 0)),
        })
    return {"experiment_id": view["experiment_id"], "status": view["status"],
            "method": view["method"], "objective": view["objective"],
            "verdict": view["verdict"], "best_label": view["best_label"],
            "values_are": "baseline row: absolute; other rows: paired change vs baseline",
            "rows": rows}


@router.get("/api/simulation-experiments/{experiment_id}/comparison")
def get_comparison(experiment_id: str, request: Request,
                   role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    return comparison(_view(request.app.state.ops, experiment_id))


@router.get("/api/simulation-experiments")
def list_experiments(request: Request, plant_id: str = "plant_a",
                     origin_run_id: str | None = Query(default=None, max_length=64),
                     origin_proposal_id: str | None = Query(default=None, max_length=64),
                     limit: int = 10,
                     role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    where, params = "plant_id = ?", [plant_id]
    if origin_run_id:
        where += " AND origin_run_id = ?"
        params.append(origin_run_id)
    if origin_proposal_id:
        where += " AND origin_proposal_id = ?"
        params.append(origin_proposal_id)
    rows = db.query_all(request.app.state.ops,
                        f"""SELECT experiment_id, created_at, station_id, objective, status,
                                   heartbeat_at, profile, seeds, origin_run_id,
                                   origin_proposal_id, summary, runtime_s
                              FROM simulation_experiments WHERE {where}
                             ORDER BY created_at DESC LIMIT ?""", (*params, limit))
    out = []
    for r in rows:
        summary = _loads(r.get("summary")) or {}
        out.append({"experiment_id": r["experiment_id"], "created_at": r["created_at"],
                    "station_id": r["station_id"], "objective": r["objective"],
                    "status": "failed" if _abandoned(r) else r["status"],
                    "profile": r["profile"], "seed_count": len(_loads(r["seeds"]) or []),
                    "origin_run_id": r["origin_run_id"],
                    "origin_proposal_id": r["origin_proposal_id"],
                    "verdict": summary.get("verdict"), "best_label": summary.get("best_label"),
                    "runtime_s": r["runtime_s"]})
    return {"plant_id": plant_id, "experiments": out, "count": len(out)}


@router.post("/api/simulation-experiments/{experiment_id}/cancel")
def cancel_experiment(experiment_id: str, request: Request,
                      role: str = Depends(require_role("manager"))) -> dict[str, Any]:
    ops = request.app.state.ops
    exp = db.query_one(ops, "SELECT status, heartbeat_at, created_at FROM "
                            "simulation_experiments WHERE experiment_id=?", (experiment_id,))
    if exp is None:
        raise HTTPException(404, f"unknown experiment '{experiment_id}'")
    if exp["status"] not in ACTIVE:
        raise HTTPException(409, f"experiment is already {exp['status']}")
    if exp["status"] == "queued" or _abandoned(exp):
        # No live worker will read the flag: settle it here.
        db.execute(ops, "UPDATE simulation_experiments SET status='cancelled', "
                        "cancel_requested=1, finished_at=?, updated_at=? "
                        "WHERE experiment_id=?", (_now(), _now(), experiment_id))
    else:
        db.execute(ops, "UPDATE simulation_experiments SET cancel_requested=1, updated_at=? "
                        "WHERE experiment_id=?", (_now(), experiment_id))
    log_action(request, ops, role, "simulation_experiment_cancelled",
               "simulation_experiment", experiment_id, {})
    return _view(ops, experiment_id)
