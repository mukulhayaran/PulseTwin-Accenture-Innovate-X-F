"""The ONLY interventions a simulation experiment may test, with their bounds.

One definition on both sides of the import wall: `serve/routers/experiments.py` validates a
request against it, and `offline/sim_experiment.py` (which may import serve/) executes
exactly what it says. An intervention is listed only if the physical twin genuinely has the
parameter it changes -- nothing here maps a request onto an unrelated knob.

`simulated_as` is shown next to every result so a reader sees what the simulator actually
changed, in words. `UNSUPPORTED` exists so an agent (and a person) asking for something the
simulator cannot do gets a named, specific refusal instead of a quiet approximation.
"""

from __future__ import annotations

from typing import Any

# Physical-twin incident scenarios an experiment may reproduce as its operating context.
# `sensor_dropout` is absent on purpose: it removes readings, not flow, so there is nothing
# for a throughput experiment to measure against.
CONTEXT_SCENARIOS = ("torque_degradation", "high_humidity", "station_breakdown",
                     "buffer_congestion", "rushed_checklist", "bad_material_lot")

INTERVENTIONS: dict[str, dict[str, Any]] = {
    "feed_buffer_delta": {
        "params": {"delta": {"type": "integer", "minimum": -4, "maximum": 8}},
        "simulated_as": "changes the capacity of the buffer FEEDING the station (the "
                        "upstream station's output buffer) by `delta` slots, floored at 1",
    },
    "cycle_time_pct": {
        "params": {"pct": {"type": "number", "minimum": -20.0, "maximum": 20.0}},
        "simulated_as": "scales the station's nominal cycle time by `pct` percent for "
                        "every unit started after the intervention",
    },
    "repair_time_pct": {
        "params": {"pct": {"type": "number", "minimum": -50.0, "maximum": 50.0}},
        "simulated_as": "scales the station's mean repair time by `pct` percent for "
                        "every repair that starts after the intervention",
    },
    "maintenance": {
        "params": {"delay_min": {"type": "integer", "minimum": 0, "maximum": 240}},
        "simulated_as": "after `delay_min`, takes the station down through the "
                        "simulator's own failure path (repair length drawn from its "
                        "configured repair distribution); the repair restores the tool, "
                        "and a tool-wear or breakdown condition at the station ends",
    },
    "mitigation": {
        "params": {"action_type": {"type": "string",
                                   "enum": ["hvac_setpoint", "maintenance_ticket",
                                            "shift_weld_load", "increase_feed_buffer",
                                            "rebalance_cycle"]}},
        "simulated_as": "applies the catalogue action's modelled effect to the matching "
                        "condition (serve/live_simulation.py::MITIGATIONS; "
                        "docs/ASSUMPTIONS.md, Mitigation effects) -- requires an incident "
                        "context that action addresses",
    },
}

UNSUPPORTED: dict[str, str] = {
    "variant_sequencing": "the simulator draws each unit's variant at random from the "
                          "day's configured mix; there is no controllable sequence to change",
    "resequence_paint_queue": "acts on scoring priority, not on a physical condition the "
                              "twin simulates",
    "take_station_offline": "a permanent station removal changes the line topology, which "
                            "the simulator does not support mid-run",
}

# Resource limits. Mirrored in configs/deployment.yaml `simulation_experiments:` -- serve/
# cannot parse YAML (the import wall), so tests/test_configs.py pins the two together.
LIMITS: dict[str, float] = {
    "max_scenarios": 5,            # including the paired baseline
    "max_seeds": 12,
    "max_horizon_s": 14400.0,      # 4 simulated hours
    "max_concurrent": 2,
    "wallclock_timeout_s": 420.0,
    "max_experiments_per_run": 3,  # per originating agent run
    "max_seeds_per_run": 48,       # seeds x scenarios, summed across a run's experiments
}

# What an experiment optimises, and the metric its deterministic verdict reads.
OBJECTIVES: dict[str, str] = {
    "throughput": "throughput_uph", "starvation": "starved_pct", "blocking": "blocked_pct",
    "downtime": "down_pct", "quality": "expected_defects", "wip": "wip_mean",
}

PROFILES: dict[str, dict[str, float]] = {
    "smoke": {"seeds": 3, "horizon_s": 3600.0},
    "standard": {"seeds": 5, "horizon_s": 7200.0},
}


def validate_intervention(item: Any) -> str | None:
    """An error string for an intervention the catalogue does not admit, or None."""
    if not isinstance(item, dict):
        return "an intervention must be an object {type, params}"
    kind = item.get("type")
    if kind in UNSUPPORTED:
        return f"'{kind}' is not supported: {UNSUPPORTED[kind]}"
    spec = INTERVENTIONS.get(kind)
    if spec is None:
        return (f"unknown intervention type '{kind}'; supported: "
                f"{sorted(INTERVENTIONS)}")
    params = item.get("params") or {}
    if not isinstance(params, dict):
        return "params must be an object"
    for key in params:
        if key not in spec["params"]:
            return f"{kind} takes no parameter '{key}'"
    for key, rule in spec["params"].items():
        if key not in params:
            return f"{kind} requires parameter '{key}'"
        value = params[key]
        if rule["type"] == "string":
            if value not in rule["enum"]:
                return f"{kind}.{key} must be one of {rule['enum']}"
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{kind}.{key} must be a number"
        if rule["type"] == "integer" and not float(value).is_integer():
            return f"{kind}.{key} must be an integer"
        if not rule["minimum"] <= value <= rule["maximum"]:
            return f"{kind}.{key} must be within [{rule['minimum']}, {rule['maximum']}]"
    return None


def label(item: dict[str, Any]) -> str:
    """A stable human label, e.g. `feed_buffer_delta(delta=4)`."""
    params = item.get("params") or {}
    inner = ", ".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{item['type']}({inner})"
