"""Independent bottleneck ground truth by throughput sensitivity.

WHY THIS MODULE EXISTS. `offline/ml/bottleneck.py` scores its detector against a
"ground truth" that is the SAME active-share matrix the detector consumes, smoothed over
eight buckets instead of one. That is better than scoring an argmax against itself, but it
is not independent: both quantities are functions of the same statistic, so a detector
that mis-measures active share is graded by a reference that mis-measures it the same way.
The reported 85.9% / 90.2% is a self-consistency number, not an accuracy.

WHAT REPLACES IT. The constraint of a production line has an operational definition that
owes nothing to any detector: it is the station whose speed most limits the line's output.
Measure it directly.

    1. Run the line.                                        -> throughput_baseline
    2. Slow station i's effective cycle time by `relative`.  -> throughput_perturbed
    3. sensitivity_i = (perturbed - baseline) / relative     [UPH per unit relative slowdown]
    4. Repeat under Common Random Numbers across many seeds.
    5. Bottleneck = argmin over i (the most NEGATIVE sensitivity: slowing it hurts most).

Sensitivity is signed and is essentially always negative -- slowing a station cannot
raise throughput. A station with sensitivity near zero is not a constraint at all: the
line absorbs its slowdown in buffer and idle time. The magnitude is directly
interpretable as "units per hour lost per 100% slowdown", so a sensitivity of -32 means
a 10% slowdown at that station costs 3.2 UPH.

COST. One ground-truth evaluation is `n_seeds * (n_stations + 1)` line runs. For plant A
at a 5-day horizon that is 30 * 31 = 930 runs of roughly 3 seconds each. It is an offline
experiment, run by hand or nightly -- never at request time, never at startup.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field

import numpy as np

from offline.benchmarks.simrun import perturb, run_line, throughput_uph
from offline.config_schema import PlantConfig

DEFAULT_RELATIVE = 0.10
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 42

# Below this |mean sensitivity| a station is not meaningfully constraining the line; we
# report it but never call it the bottleneck. Chosen as "a tenth of a unit per hour lost
# per 10% slowdown", which is inside the seed-to-seed noise of any run we do here.
NEGLIGIBLE_SENSITIVITY = 1.0


@dataclass
class StationSensitivity:
    station_id: int
    mean: float
    std: float
    ci_low: float
    ci_high: float
    n_seeds: int
    throughput_baseline_mean: float
    throughput_perturbed_mean: float
    per_seed: list[float] = field(default_factory=list)

    def to_row(self) -> dict:
        row = asdict(self)
        row.pop("per_seed")
        return row


@dataclass
class GroundTruth:
    plant_id: str
    scenario: str
    relative: float
    days: float
    seeds: list[int]
    stations: list[StationSensitivity]
    bottleneck_station: int
    runner_up_station: int
    margin: float
    separated: bool
    throughput_baseline_mean: float
    throughput_baseline_std: float

    def ranking(self) -> list[int]:
        """Station ids ordered most-constraining first."""
        return [s.station_id for s in sorted(self.stations, key=lambda s: s.mean)]

    def to_summary(self) -> dict:
        return {
            "plant_id": self.plant_id,
            "scenario": self.scenario,
            "relative_perturbation": self.relative,
            "horizon_days": self.days,
            "n_seeds": len(self.seeds),
            "seeds": self.seeds,
            "bottleneck_station": self.bottleneck_station,
            "runner_up_station": self.runner_up_station,
            "margin_uph_per_unit_relative": self.margin,
            "separated": self.separated,
            "throughput_baseline_mean_uph": self.throughput_baseline_mean,
            "throughput_baseline_std_uph": self.throughput_baseline_std,
            "ranking": self.ranking(),
        }


# ------------------------------------------------------------------ parallel execution
#
# A module-global config set by the pool initialiser, so the (large-ish) PlantConfig is
# pickled once per worker rather than once per task. On Windows the pool uses spawn, so
# the initialiser runs in a fresh interpreter and this global starts out None.

_WORKER_CFG: PlantConfig | None = None


def _init_worker(cfg: PlantConfig) -> None:
    global _WORKER_CFG
    _WORKER_CFG = cfg
    # Each worker is single-threaded on purpose: OpenMP reduction order is not
    # deterministic, and this benchmark's whole claim is reproducibility (ADR-016).
    os.environ["OMP_NUM_THREADS"] = "1"


def _baseline_task(seed: int) -> tuple[int, float]:
    assert _WORKER_CFG is not None
    return seed, throughput_uph(run_line(_WORKER_CFG, seed))


def _perturbed_task(args: tuple[int, int, float]) -> tuple[int, int, float]:
    station_id, seed, relative = args
    assert _WORKER_CFG is not None
    cfg = perturb(_WORKER_CFG, station_id, relative)
    return station_id, seed, throughput_uph(run_line(cfg, seed))


def _map(fn, items, cfg: PlantConfig, workers: int, progress: str | None):
    """Run `fn` over `items`, in a process pool when workers > 1, else inline."""
    total = len(items)
    out = []
    if workers <= 1:
        _init_worker(cfg)
        for i, item in enumerate(items, 1):
            out.append(fn(item))
            if progress and (i % 25 == 0 or i == total):
                print(f"  {progress}: {i}/{total}", flush=True)
        return out
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_worker, initargs=(cfg,)
    ) as pool:
        for i, value in enumerate(pool.map(fn, items, chunksize=1), 1):
            out.append(value)
            if progress and (i % 25 == 0 or i == total):
                print(f"  {progress}: {i}/{total}", flush=True)
    return out


# ------------------------------------------------------------------------- the estimate


def bootstrap_ci(
    values: np.ndarray, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of the mean. Deterministic given `seed`."""
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return float("nan"), float("nan")
    if values.size == 1:
        return float(values[0]), float(values[0])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, values.size, size=(resamples, values.size))
    means = values[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def compute(
    cfg: PlantConfig,
    seeds: list[int],
    relative: float = DEFAULT_RELATIVE,
    station_ids: list[int] | None = None,
    workers: int = 1,
    scenario: str = "baseline",
    verbose: bool = True,
) -> GroundTruth:
    """Throughput-sensitivity ground truth for one config.

    `cfg` must ALREADY carry the scenario deltas and the intended horizon; this function
    perturbs on top of whatever it is handed. `scenario` is a label recorded in the
    output, not an instruction.
    """
    ids = station_ids if station_ids is not None else [s.id for s in cfg.stations]
    days = cfg.simulation.duration_days

    if verbose:
        print(
            f"[ground-truth] {cfg.plant_id}/{scenario}: {len(ids)} stations x "
            f"{len(seeds)} seeds, +{relative:.0%} perturbation, {days}-day horizon, "
            f"{len(seeds) * (len(ids) + 1)} line runs on {workers} worker(s)",
            flush=True,
        )

    base_pairs = _map(_baseline_task, list(seeds), cfg, workers, "baseline" if verbose else None)
    baseline = {seed: value for seed, value in base_pairs}
    base_values = np.array([baseline[s] for s in seeds], dtype=np.float64)

    tasks = [(station_id, seed, relative) for station_id in ids for seed in seeds]
    results = _map(_perturbed_task, tasks, cfg, workers, "perturbed" if verbose else None)

    by_station: dict[int, dict[int, float]] = {station_id: {} for station_id in ids}
    for station_id, seed, value in results:
        by_station[station_id][seed] = value

    stations: list[StationSensitivity] = []
    for station_id in ids:
        perturbed = np.array([by_station[station_id][s] for s in seeds], dtype=np.float64)
        # Paired difference: CRN means base and perturbed share a seed, so subtract
        # elementwise rather than comparing two independent means.
        sens = (perturbed - base_values) / relative
        low, high = bootstrap_ci(sens)
        stations.append(
            StationSensitivity(
                station_id=station_id,
                mean=float(sens.mean()),
                std=float(sens.std(ddof=1)) if sens.size > 1 else 0.0,
                ci_low=low,
                ci_high=high,
                n_seeds=len(seeds),
                throughput_baseline_mean=float(base_values.mean()),
                throughput_perturbed_mean=float(perturbed.mean()),
                per_seed=[float(v) for v in sens],
            )
        )

    ordered = sorted(stations, key=lambda s: s.mean)
    top, second = ordered[0], ordered[1] if len(ordered) > 1 else ordered[0]
    margin = second.mean - top.mean

    # "Separated" means the winner's bootstrap CI does not overlap the runner-up's AND
    # the winner is meaningfully constraining at all. An unseparated scenario is not a
    # failure -- it is the honest description of an ambiguous line, and the detector
    # benchmark reports those scenarios separately rather than folding them in.
    separated = bool(
        top.ci_high < second.ci_low and abs(top.mean) >= NEGLIGIBLE_SENSITIVITY
    )

    return GroundTruth(
        plant_id=cfg.plant_id,
        scenario=scenario,
        relative=relative,
        days=days,
        seeds=list(seeds),
        stations=stations,
        bottleneck_station=top.station_id,
        runner_up_station=second.station_id,
        margin=float(margin),
        separated=separated,
        throughput_baseline_mean=float(base_values.mean()),
        throughput_baseline_std=float(base_values.std(ddof=1)) if base_values.size > 1 else 0.0,
    )


def seed_list(n: int, start: int = 1000) -> list[int]:
    """Deterministic seed set. Offset from the configs' own 42/43 so the ground truth is
    not evaluated on the single realisation the shipped artifact was built from."""
    return [start + i for i in range(n)]
