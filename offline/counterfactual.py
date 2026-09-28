"""Condition hypotheses and twin mitigations for a watch firing (ADR-055, ADR-064).

`hypothesis` maps a firing's own measured evidence (never the injected ground truth) to
the scenario and severity the physical twin should reproduce; `_reseed` and `_mitigate`
fork and treat an `offline.live_twin.LiveTwinEngine` copy. `offline/sim_experiment.py`
runs its paired-seed experiments on these, and `offline/benchmarks/agent_eval.py` scores
root causes against `hypothesis`.

The ADR-055 forecast job that used to be spawned from `/api/proposals` was retired with
the proposal workflow (ADR-073); the Incident Commander's experiments replaced it.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from offline.live_twin.engine import LiveTwinEngine
from offline.live_twin.incidents import MAX_HUMIDITY_ADD_PCT, MAX_TOOL_DEFICIT
from serve.live_simulation import MITIGATIONS

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
        state = str(evidence.get("stalled_state") or "unknown").upper()
        seen = (f"silent {float(evidence['silent_s']) / 60:.0f} min, last state {state}"
                if evidence.get("signal") == "silence"
                else f"station reported {state}; stalled share {share:.2f}")
        down = evidence.get("down_stations") or []
        if evidence.get("stalled_state") == "down" or down:
            if down and evidence.get("stalled_state") != "down":
                seen += f"; S{', S'.join(map(str, down))} reported DOWN"
            return ("station_breakdown", min(1.0, share), seen)
        return ("buffer_congestion", min(MAX_CONGESTION, share),
                f"{seen} (severity capped {MAX_CONGESTION})")
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
    engine.apply_congestion_pull(now)   # a milder congestion slows the next station less
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
