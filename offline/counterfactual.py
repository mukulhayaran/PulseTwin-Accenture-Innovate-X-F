"""Monte Carlo counterfactual for one watch firing (ADR-055).

Nothing an agent proposes reaches a human without passing through here. For the condition
the watch detected, the physical twin (`offline.live_twin.LiveTwinEngine`) is warmed up
once, the condition is reproduced at the firing station, and the line is then FORKED --
`copy.deepcopy` of the whole engine, every RNG reseeded in place -- into `replicas`
futures per arm. One arm is doing nothing; the others apply each catalogue action that
has a physical effect on that condition. Replica k uses the same seed in every arm
(common random numbers), so the action-vs-nothing difference is PAIRED and its interval
is honest about how much of the gap is the action and how much is the line's own noise.

What this is not: a fork of the live stream-replay run. `serve/live_simulation.py` replays
the committed stream; this reproduces the DETECTED condition on the physical twin at a
severity inferred from the firing's own evidence (never from the injected ground truth).
Every output is `provenance: "inferred"` and carries its interval (contract #1). No
dollar figure is emitted (contract #2).

Spawned detached by `serve/live_simulation.py::_on_firing`, exactly like `genai.triage`;
writes `<jobs>/<proposal_id>.forecast.json`, which `/api/proposals` reads.

    python -m offline.counterfactual --plant plant_a --detector tool_wear --station 3 \
        --evidence '{"deviation_pct": -12.4}' --budget-threshold 0.3 --out f.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from offline.benchmarks.simrun import with_horizon
from offline.config_schema import load_plant_config
from offline.live_twin.engine import LiveTwinEngine
from offline.live_twin.incidents import MAX_HUMIDITY_ADD_PCT, MAX_TOOL_DEFICIT, Incident
from serve.live_simulation import MITIGATIONS

ROOT = Path(__file__).resolve().parents[1]
DO_NOTHING = "do_nothing"
CHECKPOINT_S = 600.0
BOOTSTRAP = 2000
# Congestion at severity 1.0 is a zero-capacity buffer: the line deadlocks and the DES
# clock jumps to the next failure event. Measured on plant_a, 2026-09-25. A detector that
# sees events still arriving is by definition not looking at a deadlocked line.
MAX_CONGESTION = 0.8
# Slots `increase_feed_buffer` adds to the buffer AHEAD OF THE DOWNSTREAM station -- the
# firing station's own output buffer, which is what the catalogue action means and what
# the deterministic plan proposes (+4, genai/offline_triage.py). A modelled assumption
# (docs/ASSUMPTIONS.md, Mitigation effects): the agent's own buffer_delta arrives after
# this job has already started.
FEED_BUFFER_SLOTS = 4


def hypothesis(detector: str, evidence: dict[str, Any]) -> tuple[str, float, str]:
    """(scenario, severity, basis): which physical condition the firing implies, and how
    strongly, from the firing's MEASURED evidence. Each severity map is linear and is a
    modelled assumption, stated in `basis` so the UI can show it."""
    if detector == "tool_wear":
        dev = abs(float(evidence.get("deviation_pct") or 0.0))
        sev = min(1.0, dev / (MAX_TOOL_DEFICIT * 100.0))
        return ("torque_degradation", sev,
                f"|{dev:.1f}%| signal deviation / {MAX_TOOL_DEFICIT * 100:.0f}% full-wear deficit")
    if detector == "paint_env":
        excess = float(evidence.get("humidity_excess_over_ambient_pct") or 0.0)
        sev = min(1.0, excess / MAX_HUMIDITY_ADD_PCT)
        return ("high_humidity", sev,
                f"{excess:.1f} pts humidity over ambient / {MAX_HUMIDITY_ADD_PCT:.0f} pts full severity")
    if detector == "starvation":
        share = float(evidence.get("stalled_share") or 0.0)
        if evidence.get("stalled_state") == "down":
            return ("station_breakdown", min(1.0, share),
                    f"station reported DOWN; stalled share {share:.2f}")
        return ("buffer_congestion", min(MAX_CONGESTION, share),
                f"station reported BLOCKED; stalled share {share:.2f} (capped {MAX_CONGESTION})")
    raise ValueError(f"unknown detector '{detector}'")


def _reseed(engine: LiveTwinEngine, replica: int) -> None:
    # In place: the simulator, signal generator and pool hold references to these objects.
    rngs = (engine.rng_pool, engine.rng_des, engine.rng_latent,
            engine.rng_phys, engine.rng_causal, engine.rng_insp)
    for i, rng in enumerate(rngs):
        rng.bit_generator.state = np.random.default_rng([replica, i]).bit_generator.state


def _mitigate(engine: LiveTwinEngine, scenario: str, action_type: str,
              station_id: int) -> None:
    """Remove `factor` of the condition's effect -- the same modelled assumption the live
    twin applies on approval (serve/live_simulation.py::MITIGATIONS) -- and, for a buffer
    action, add physical slots to the station's output buffer (the downstream feed).

    Deliberately NOT `engine._apply_physical_state()`: that also re-forces a breakdown
    while a breakdown incident is active, so a mitigation applied to a station that had
    already been repaired knocked it down a second time (-11.7 UPH on the first run).
    """
    factor = float(MITIGATIONS[action_type]["factor"])
    for incident in engine.incidents.active(engine.now):
        if incident.scenario != scenario:
            continue
        if factor >= 1.0:
            incident.ended_at = engine.now
        else:
            incident.severity *= 1.0 - factor
    stations = engine.sim.stations
    if action_type == "increase_feed_buffer":
        engine._base_capacity[station_id] += FEED_BUFFER_SLOTS
    now = engine.now
    for rt in stations:
        sid = rt.cfg.id
        rt.capacity = engine.incidents.buffer_capacity(sid, engine._base_capacity[sid], now)
        if sid in engine._base_checklist:
            rt.cfg.checklist_nominal_s = (
                engine._base_checklist[sid] * engine.incidents.checklist_scale(sid, now))
        # A station BLOCKED on a buffer that just grew would otherwise wait for the next
        # downstream pull to notice the room.
        if rt.held is not None and rt.state != "down":
            engine.sim._release_held(rt, now)


def simulated_as(scenario: str, action_type: str, station_id: int) -> str:
    """What the fork actually changed, in words -- shown beside the agent's own params so
    a difference between what was proposed and what was simulated is visible."""
    share = float(MITIGATIONS[action_type]["factor"])
    what = (f"ends the {scenario.replace('_', ' ')}" if share >= 1.0
            else f"removes {share:.0%} of the {scenario.replace('_', ' ')}")
    if action_type == "increase_feed_buffer":
        what += f"; +{FEED_BUFFER_SLOTS} slots on S{station_id}'s output buffer"
    return what


def _ci(values: np.ndarray, rng: np.random.Generator) -> dict[str, float]:
    """Mean with a 95% percentile-bootstrap interval of the mean."""
    means = rng.choice(values, size=(BOOTSTRAP, len(values)), replace=True).mean(axis=1)
    return {"mean": round(float(values.mean()), 3),
            "ci_low": round(float(np.percentile(means, 2.5)), 3),
            "ci_high": round(float(np.percentile(means, 97.5)), 3)}


def _run_arm(forked: LiveTwinEngine, scenario: str, action: str, station_id: int,
             replicas: int, horizon_s: float) -> dict[str, np.ndarray]:
    """Defects are EXPECTED defects -- the sum of each inspected unit's propensity --
    rather than the Bernoulli draw on it: the same expectation with far less variance
    (conditional Monte Carlo), which matters at ~0.3 true defects an hour."""
    steps = int(horizon_s // CHECKPOINT_S)
    units, defects, alerts = [], [], []
    trajectory = np.zeros((replicas, steps))
    for k in range(replicas):
        f = copy.deepcopy(forked)
        _reseed(f, k)
        if action != DO_NOTHING:
            _mitigate(f, scenario, action, station_id)
        u0, a0, expected = f.units_completed, f.alerts_opened, 0.0
        for s in range(steps):
            for e in f.advance(CHECKPOINT_S):
                if e["event_type"] == "inspection_outcome":
                    expected += float(e["propensity"])
            trajectory[k, s] = f.units_completed - u0
        units.append(f.units_completed - u0)
        defects.append(expected)
        alerts.append(f.alerts_opened - a0)
    return {"units": np.array(units, float), "defects": np.array(defects, float),
            "alerts": np.array(alerts, float), "trajectory": trajectory}


def forecast(plant_id: str, detector: str, station_id: int, evidence: dict[str, Any],
             budget_threshold: float, replicas: int = 8, horizon_s: float = 7200.0,
             onset_s: float = 1800.0, seed: int = 42) -> dict[str, Any]:
    started = time.time()
    scenario, severity, basis = hypothesis(detector, evidence)
    cfg = with_horizon(load_plant_config(ROOT / "configs" / f"{plant_id}.yaml"), 3.0)
    if station_id not in {s.id for s in cfg.stations}:
        raise ValueError(f"station {station_id} is not in {plant_id}")

    twin = LiveTwinEngine(cfg, seed=seed, artifacts=ROOT / "artifacts",
                          budget_threshold=budget_threshold)
    twin.start()
    # Humidity is a zone condition; everything else is local to the firing station.
    twin.inject(Incident(scenario, severity,
                         station_id=None if scenario == "high_humidity" else station_id))
    twin.advance(onset_s)  # let the condition establish before the decision point

    actions = [DO_NOTHING] + [a for a, m in MITIGATIONS.items() if scenario in m["scenarios"]]
    hours = horizon_s / 3600.0
    boot = np.random.default_rng(seed)
    runs: dict[str, dict[str, np.ndarray]] = {}
    arms: list[dict[str, Any]] = []
    for action in actions:
        if action != DO_NOTHING and MITIGATIONS[action]["scope"] == "risk":
            arms.append({"action_type": action, "modelled": False,
                         "reason": "acts on scoring priority, not on a physical condition "
                                   "the twin simulates"})
            continue
        run = runs[action] = _run_arm(twin, scenario, action, station_id, replicas,
                                      horizon_s)
        traj = run["trajectory"]
        arm: dict[str, Any] = {
            "action_type": action,
            "modelled": True,
            "simulated_as": (None if action == DO_NOTHING
                             else simulated_as(scenario, action, station_id)),
            "throughput_uph": _ci(run["units"] / hours, boot),
            "expected_defects": _ci(run["defects"], boot),
            "alerts": _ci(run["alerts"], boot),
            "trajectory": {
                "t_min": [round((s + 1) * CHECKPOINT_S / 60) for s in range(traj.shape[1])],
                "p10": np.percentile(traj, 10, axis=0).round(1).tolist(),
                "p50": np.percentile(traj, 50, axis=0).round(1).tolist(),
                "p90": np.percentile(traj, 90, axis=0).round(1).tolist(),
            },
        }
        if action != DO_NOTHING:
            base = runs[DO_NOTHING]
            d_uph = _ci((run["units"] - base["units"]) / hours, boot)
            d_def = _ci(run["defects"] - base["defects"], boot)
            arm["vs_do_nothing"] = {
                "throughput_uph": d_uph,
                "expected_defects": d_def,
                # The paired interval excludes zero on at least one outcome.
                "distinguishable": bool(d_uph["ci_low"] > 0 or d_uph["ci_high"] < 0
                                        or d_def["ci_low"] > 0 or d_def["ci_high"] < 0),
            }
        arms.append(arm)

    # Rank by expected throughput gain, then by defects avoided. An action whose interval
    # includes zero on both still ranks, but says so -- the human sees the overlap.
    def key(a: dict[str, Any]) -> tuple[float, float]:
        v = a.get("vs_do_nothing") or {}
        return (-(v.get("throughput_uph") or {}).get("mean", 0.0),
                (v.get("expected_defects") or {}).get("mean", 0.0))
    ranked = sorted((a for a in arms if a["modelled"] and a["action_type"] != DO_NOTHING),
                    key=key)
    return {
        "version": 1,
        "status": "done",
        "plant_id": plant_id,
        "detector": detector,
        "station_id": station_id,
        "provenance": "inferred",
        "hypothesis": {"scenario": scenario, "severity": round(severity, 3), "basis": basis},
        "method": {"twin": "offline.live_twin.LiveTwinEngine", "replicas": replicas,
                   "horizon_s": horizon_s, "onset_s": onset_s, "seed": seed,
                   "paired": True, "interval": "95% percentile bootstrap of the mean",
                   "mitigation_effects": "docs/ASSUMPTIONS.md, Mitigation effects"},
        "ranking": [a["action_type"] for a in ranked],
        "arms": arms,
        "elapsed_s": round(time.time() - started, 1),
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)  # a reader never sees half a file


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--plant", required=True)
    p.add_argument("--detector", required=True, choices=("starvation", "paint_env", "tool_wear"))
    p.add_argument("--station", type=int, required=True)
    p.add_argument("--evidence", required=True, help="the firing's evidence, as JSON")
    p.add_argument("--budget-threshold", type=float, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--replicas", type=int, default=8)
    p.add_argument("--horizon-s", type=float, default=7200.0)
    args = p.parse_args(argv)

    _write(args.out, {"version": 1, "status": "running"})
    try:
        result = forecast(args.plant, args.detector, args.station, json.loads(args.evidence),
                          args.budget_threshold, replicas=args.replicas,
                          horizon_s=args.horizon_s)
    except Exception as exc:  # noqa: BLE001 - the row must never sit at `running`
        _write(args.out, {"version": 1, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        raise
    _write(args.out, result)
    print(f"[counterfactual] {args.out.name} done in {result['elapsed_s']}s, "
          f"ranking={result['ranking']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
