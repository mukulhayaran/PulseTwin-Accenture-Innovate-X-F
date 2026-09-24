"""Common Random Numbers helpers for perturbation experiments.

Everything here is a thin wrapper over the EXISTING simulator (`offline.simulator`). No
simulation logic is reimplemented -- the point of this module is only to (a) construct the
random streams identically across paired runs so that a throughput difference is caused by
the perturbation rather than by the noise, and (b) produce a perturbed copy of a config
without mutating the caller's.

WHY CRN MATTERS HERE. A 5-day line run has a throughput spread of order a unit per hour
across seeds. The throughput effect of slowing ONE station out of thirty by 10% is of the
same order. Comparing an independently-seeded baseline against an independently-seeded
perturbed run therefore measures mostly noise. Under CRN the two runs draw from the SAME
streams, so the per-seed difference isolates the treatment and the variance of that
difference drops sharply.

CRN IS APPROXIMATE IN A DES, AND WE SAY SO. Changing a cycle time changes event ordering,
so after the first perturbed completion the two runs consume their shared streams at
slightly different points -- the coupling degrades over the horizon rather than holding
exactly. That is the standard, accepted limitation of CRN in discrete-event simulation,
and it is why we still average over many seeds and bootstrap a confidence interval rather
than trusting a single paired difference. See docs/BOTTLENECK_BENCHMARK.md.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np

from offline.config_schema import PlantConfig
from offline.simulator.line_model import LineSimulator, SimResult
from offline.simulator.operators import OperatorPool

# generate.py spawns SIX streams from the config seed and hands the first two to the
# operator pool and the DES. Spawning the same six here means `run_line(cfg, seed)` with
# seed == cfg.simulation.seed reproduces the pipeline's own line run exactly, rather than
# being a similar-but-different realisation.
_SPAWN_COUNT = 6


def run_line(cfg: PlantConfig, seed: int, instrument: bool = False) -> SimResult:
    """Run the DES for one config under one seed. Timing only -- no signals, no labels.

    `instrument` additionally records output-buffer occupancy and active-period
    statistics, which two of the benchmarked detectors need. It does not change the
    simulation, only what is measured from it -- proven by
    tests/test_bottleneck_benchmark.py::test_instrumentation_does_not_change_the_run.
    """
    root = np.random.SeedSequence(seed)
    streams = [np.random.default_rng(s) for s in root.spawn(_SPAWN_COUNT)]
    rng_pool, rng_des = streams[0], streams[1]
    return LineSimulator(
        cfg, OperatorPool(cfg, rng_pool), rng_des, instrument=instrument
    ).run()


def throughput_uph(result: SimResult) -> float:
    """Units per hour completed after warm-up, over the post-warm-up horizon."""
    hours = result.horizon_s / 3600.0
    if hours <= 0:
        return 0.0
    return result.completed_units / hours


def perturb(cfg: PlantConfig, station_id: int, relative: float) -> PlantConfig:
    """Deep copy of `cfg` with one station's nominal cycle time scaled by (1 + relative).

    Scaling `nominal_cycle_s` rather than injecting a multiplier downstream keeps the
    perturbation inside the config contract: variant multipliers, tool wear, cycle CV and
    the manual-station checklist coupling all continue to apply on top of it exactly as
    they do for any other station. A perturbed config is still a valid config.
    """
    out = copy.deepcopy(cfg)
    for station in out.stations:
        if station.id == station_id:
            station.nominal_cycle_s = station.nominal_cycle_s * (1.0 + relative)
            return out
    raise ValueError(f"station {station_id} not in {cfg.plant_id}")


def with_horizon(cfg: PlantConfig, days: float) -> PlantConfig:
    """Deep copy of `cfg` with a shorter (or longer) simulated horizon."""
    out = copy.deepcopy(cfg)
    out.simulation.duration_days = days
    return out


@dataclass(frozen=True)
class Scenario:
    """A controlled bottleneck scenario: a named set of cycle-time deltas over a config.

    `deltas` maps station_id -> relative cycle-time change applied BEFORE the experiment
    starts. It is the scenario, not the probe: the sensitivity perturbation is applied on
    top of an already-scenarioed config.
    """

    key: str
    plant_id: str
    description: str
    deltas: dict[int, float]

    def apply(self, cfg: PlantConfig) -> PlantConfig:
        out = copy.deepcopy(cfg)
        by_id = {s.id: s for s in out.stations}
        for station_id, rel in self.deltas.items():
            if station_id not in by_id:
                raise ValueError(
                    f"scenario '{self.key}': station {station_id} not in {cfg.plant_id}"
                )
            by_id[station_id].nominal_cycle_s *= (1.0 + rel)
        return out


# ---------------------------------------------------------------------------- scenarios
#
# Plant A ships with S12 (61.0s flat) as the normal-day constraint and S19 (55.0s x 1.50
# for EV) as the EV-heavy-day constraint -- see configs/plant_a.yaml's header. The
# scenarios below do not invent a bottleneck out of nothing; they strengthen one of the
# line's OWN constraints so a single station dominates unambiguously, plus one scenario
# that deliberately makes two stations near-equal.
#
# Plant B's body-zone constraint is S16 "Body Completion" (70.0s); S34 sits in late final
# assembly. Those give the upstream/downstream pair the brief asks for.

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="baseline",
        plant_id="plant_a",
        description=(
            "Config as shipped. The constraint is whatever the line's own cycle times "
            "and variant mix make it -- no scenario delta applied."
        ),
        deltas={},
    ),
    Scenario(
        key="s12_constraint",
        plant_id="plant_a",
        description=(
            "S12 Body Completion slowed 15%. Strengthens plant A's documented normal-day "
            "constraint into an unambiguous single bottleneck."
        ),
        deltas={12: 0.15},
    ),
    Scenario(
        key="s19_constraint",
        plant_id="plant_a",
        description=(
            "S19 Battery/Drivetrain Mount slowed 15%. Moves the constraint into final "
            "assembly, downstream of the tight paint buffers."
        ),
        deltas={19: 0.15},
    ),
    Scenario(
        key="ambiguous_s12_s19",
        plant_id="plant_a",
        description=(
            "S12 +14% and S19 +12% together: two near-equal constraints in different "
            "zones. Ground truth here is expected to be unstable across seeds, and a "
            "detector that is confidently wrong is worse than one that is uncertain."
        ),
        deltas={12: 0.14, 19: 0.12},
    ),
    Scenario(
        key="baseline",
        plant_id="plant_b",
        description="Config as shipped.",
        deltas={},
    ),
    Scenario(
        key="s16_upstream",
        plant_id="plant_b",
        description=(
            "S16 Body Completion slowed 15% -- an UPSTREAM constraint, with the whole "
            "paint shop and final assembly downstream of it."
        ),
        deltas={16: 0.15},
    ),
    Scenario(
        key="s34_downstream",
        plant_id="plant_b",
        description=(
            "A late final-assembly station slowed 15% -- a DOWNSTREAM constraint, which "
            "starves nothing and blocks almost everything upstream of it."
        ),
        deltas={34: 0.15},
    ),
)


def scenarios_for(plant_id: str) -> list[Scenario]:
    return [s for s in SCENARIOS if s.plant_id == plant_id]


def get_scenario(plant_id: str, key: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.plant_id == plant_id and scenario.key == key:
            return scenario
    known = [s.key for s in scenarios_for(plant_id)]
    raise ValueError(f"no scenario '{key}' for {plant_id}; known: {known}")
