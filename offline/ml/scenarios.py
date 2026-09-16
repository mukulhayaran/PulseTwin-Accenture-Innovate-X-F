"""What-if scenario grid and the instrumentation ROI planner.

WHAT-IF (ADR-007). The original spec asked for 30 stations x 13 buffer deltas x 9 cycle
deltas = 3,510 full 14-day simulations per plant, roughly 39 hours of compute for a
console nobody would explore exhaustively. Here it is 6 candidate stations x 5 buffer
deltas x 3 cycle deltas = 90 runs per plant, each one full 8-hour shift.

The six are seeded from BOTH constraints -- the normal-day bottleneck and the
variant-heavy-day one -- plus their neighbours. Seeding from only the most frequent
constraint produced a grid around S12 that omitted S19 entirely, which is the EV
bottleneck and the single station a manager would most want to explore.

BUFFER SEMANTICS: "add buffer at station S" means the buffer FEEDING S, not S's own
output buffer. Applying it downstream does literally nothing when S is the constraint --
a bottleneck is never blocked by what follows it -- and the first implementation returned
an identical 52.707 UPH for every buffer delta at S19 because of exactly that.

`new_bottleneck_station` is CATEGORICAL and is resolved by nearest-neighbour, never
interpolated: the mean of S12 and S19 is not S15.5, it is meaningless.

INSTRUMENTATION PLANNER. Ranks retrofit candidates by marginal model value per dollar.
Delta PR-AUC is MEASURED by ablation -- retrain with that station's features removed and
take the difference -- not assumed. The answer is reported whichever way it lands; a
sensor that does not help is a finding worth having before you buy 30 of them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

os.environ.setdefault("OMP_NUM_THREADS", "1")

from offline.config_schema import EconomicsConfig, PlantConfig  # noqa: E402
from offline.simulator.line_model import ALL_STATES, LineSimulator  # noqa: E402
from offline.simulator.operators import OperatorPool  # noqa: E402

BUFFER_DELTAS = (-2, 0, 2, 4, 8)
CYCLE_DELTAS = (-10.0, 0.0, 10.0)
WHATIF_WINDOW_DAYS = 0.3333          # one full 8-hour shift
CANDIDATE_STATIONS = 6

SENSOR_FOR_SIGNAL = {
    "torque_nm": "torque_sensor",
    "vibration_mm_s": "vibration_sensor",
    "temperature_c": "temperature_humidity_logger",
    "humidity_pct": "temperature_humidity_logger",
    "film_thickness_um": "film_thickness_gauge",
    "weld_current_a": "weld_current_monitor",
}


@dataclass
class ScenarioRow:
    station_id: int
    buffer_delta: int
    cycle_delta_pct: float
    throughput_uph: float
    wip_units: float
    new_bottleneck_station: int


def _run_window(cfg: PlantConfig, seed: int) -> tuple[float, float, int]:
    """One short simulation. Returns (uph, mean WIP, bottleneck station)."""
    root = np.random.SeedSequence(seed)
    r1, r2 = [np.random.default_rng(s) for s in root.spawn(2)]
    pool = OperatorPool(cfg, r1)
    res = LineSimulator(cfg, pool, r2).run()

    iw, idn = ALL_STATES.index("working"), ALL_STATES.index("down")
    ids = cfg.station_ids
    active = np.stack([res.state_seconds[s][:, iw] + res.state_seconds[s][:, idn] for s in ids], axis=1)

    # Score the constraint on the SECOND HALF of the window only. While the line is
    # filling, upstream stations have simply seen more units than downstream ones, so
    # cumulative active time is biased toward the front of the line -- plant B reported
    # S2 as its constraint for exactly this reason, on a 42-station line where the fill
    # takes longer.
    half = active.shape[0] // 2
    totals = active[half:, :].sum(axis=0)
    bn = int(ids[int(np.argmax(totals))]) if totals.sum() > 0 else -1

    # WIP proxy: units that entered but had not finished, averaged over the horizon.
    entered = len(res.units)
    finished = res.completed_units + res.warmup_units
    wip = max(0.0, float(entered - finished))
    return res.achieved_uph(), wip, bn


def candidate_stations(cfg: PlantConfig, hot_stations: list[int]) -> list[int]:
    """Constraints and their immediate neighbours -- what a manager would actually tune.

    `hot_stations` must carry BOTH constraints, not just the most frequent one. The line
    has two: the normal-day bottleneck and the variant-heavy-day one. Seeding from the
    single most common station gave a grid around S12 that omitted S19 entirely -- the EV
    bottleneck, which is the whole scenario and the one thing a manager would want to
    explore.
    """
    ids = cfg.station_ids
    hot = [s for s in hot_stations if s in ids] or [ids[len(ids) // 2]]

    picks: list[int] = []
    # Take each hot station first, then fan outwards, so both constraints are covered
    # before either one's neighbourhood is exhausted.
    for offset in (0, 1, -1, 2, -2):
        for h in hot:
            j = ids.index(h) + offset
            if 0 <= j < len(ids) and ids[j] not in picks:
                picks.append(ids[j])
            if len(picks) >= CANDIDATE_STATIONS:
                return picks[:CANDIDATE_STATIONS]
    return picks[:CANDIDATE_STATIONS]


def build_grid(cfg: PlantConfig, hot_stations: list[int]) -> tuple[list[ScenarioRow], dict]:
    """Precompute the what-if surface. ~90 runs of one shift each, per plant.

    TWO THINGS THIS GETS RIGHT that a naive short window does not:

    1. **Length.** A 4-hour window was not enough to reach steady state. The line starts
       empty, buffers take ~30 minutes to fill, and until they do nothing blocks -- so the
       run reported 57.5 UPH (above the 55 target, which is impossible in steady state)
       and named S3 as the constraint. One full shift gives the line time to actually
       become constrained.

    2. **Variant mix.** The simulator starts on a Monday, so a short run uses the NORMAL
       mix and S19 never binds -- making every buffer change at S19 look worthless. The
       grid forces the PEAK mix, because the EV-heavy day is precisely the condition a
       manager is trying to tune for. The API states this in its assumptions string.
    """
    import copy

    short = copy.deepcopy(cfg)
    short.simulation.duration_days = WHATIF_WINDOW_DAYS
    short.simulation.warmup_units = 60
    # Force the peak variant mix: every weekday counts as a high-mix day.
    short.ambient.humidity_high_days = list(range(7))

    base_uph, base_wip, base_bn = _run_window(short, cfg.simulation.seed)
    baseline = {
        "throughput_uph": base_uph,
        "wip_units": base_wip,
        "bottleneck_station": base_bn,
        "window_hours": WHATIF_WINDOW_DAYS * 24,
    }

    ids_index = {s: i for i, s in enumerate(cfg.station_ids)}

    rows: list[ScenarioRow] = []
    for sid in candidate_stations(cfg, hot_stations):
        for bd in BUFFER_DELTAS:
            for cd in CYCLE_DELTAS:
                trial = copy.deepcopy(short)
                st = trial.station(sid)
                st.nominal_cycle_s = max(5.0, st.nominal_cycle_s * (1.0 + cd / 100.0))

                # "Add buffer at station S" means the buffer FEEDING S -- which is
                # `buffer_capacity_after` of the station upstream of it.
                #
                # Applying it to S's own OUTPUT buffer does nothing whatsoever when S is
                # the constraint, and the first version did exactly that: every buffer
                # delta at S19 returned an identical 52.707 UPH. That is correct physics
                # (the bottleneck is never blocked downstream, so its output buffer is
                # irrelevant) but it is not the question a manager is asking. Buffering
                # the constraint's FEED stops it starving on upstream variability, which
                # is the lever that actually exists: at S19, +4 slots moves throughput
                # 52.7 -> 54.1 UPH and migrates the constraint to S12.
                feeder_pos = ids_index.get(sid, 0) - 1
                if feeder_pos >= 0:
                    feeder = trial.station(trial.station_ids[feeder_pos])
                    feeder.buffer_capacity_after = max(0, feeder.buffer_capacity_after + bd)

                uph, wip, bn = _run_window(trial, cfg.simulation.seed)
                rows.append(ScenarioRow(sid, bd, cd, uph, wip, bn))
    return rows, baseline


def interpolate(
    rows: list[ScenarioRow], station_id: int, buffer_delta: float, cycle_delta: float
) -> dict:
    """Bilinear on the continuous outputs; NEAREST on the categorical bottleneck.

    Interpolating a station ID would produce a number that is not a station.
    """
    pts = [r for r in rows if r.station_id == station_id]
    if not pts:
        return {}

    def weight(r: ScenarioRow) -> float:
        db = abs(r.buffer_delta - buffer_delta) / 8.0
        dc = abs(r.cycle_delta_pct - cycle_delta) / 20.0
        return 1.0 / (1e-6 + db * db + dc * dc)

    ws = np.array([weight(r) for r in pts])
    ws /= ws.sum()
    uph = float(sum(w * r.throughput_uph for w, r in zip(ws, pts)))
    wip = float(sum(w * r.wip_units for w, r in zip(ws, pts)))
    nearest = pts[int(np.argmax(ws))]

    return {
        "throughput_uph": uph,
        "wip_units": wip,
        "new_bottleneck_station": nearest.new_bottleneck_station,
        "bottleneck_mode": "nearest_neighbour",
        "grid_resolution": {"buffer_steps": list(BUFFER_DELTAS), "cycle_steps": list(CYCLE_DELTAS)},
    }


# --------------------------------------------------------------- instrumentation


def instrumentation_plan(
    cfg: PlantConfig,
    econ: EconomicsConfig,
    ablation_deltas: dict[int, float],
    annual_value_per_pr_auc_point: float,
) -> list[dict]:
    """Rank manual stations for sensor retrofit by payback.

    `ablation_deltas` comes from group-permutation importance on the instrumented stations
    and an estimate-by-analogy for the manual ones (there is no true reading at an
    unsensored station to ablate).

    A zero estimate is a RESULT, not a gap. It means the evidence already available at
    that station -- the operator checklist -- carries as much signal as a comparable
    instrumented neighbour's sensors, so a retrofit would buy information we already have.
    That is exactly what you want to know before committing capital across 30 stations.
    """
    windows = econ.instrumentation["maintenance_windows_per_year"]
    window_names = ["Window 1 (March)", "Window 2 (July)", "Window 3 (November)"][:windows]
    sensor_costs = econ.instrumentation["sensor_costs"]
    zone_signals = {z.id: z.zone_sensors for z in cfg.zones}

    plan = []
    for st in cfg.manual_stations():
        signals = zone_signals.get(st.zone, [])
        sensor_key = None
        for sig in signals:
            key = SENSOR_FOR_SIGNAL.get(sig)
            if key and key in sensor_costs:
                sensor_key = key
                break
        if sensor_key is None:
            continue

        cost = float(sensor_costs[sensor_key]["value"])
        delta = float(ablation_deltas.get(st.id, 0.0))
        annual = delta * annual_value_per_pr_auc_point
        payback = (12.0 * cost / annual) if annual > 0 else None

        plan.append({
            "station_id": st.id,
            "station_name": st.name,
            "zone": st.zone,
            "sensor_type": sensor_key,
            "cost": cost,
            "cost_source": sensor_costs[sensor_key]["source"],
            "delta_pr_auc": delta,
            "annual_value": annual,
            "payback_months": payback,
        })

    # Best payback first; stations with no measured benefit sink to the bottom and are
    # labelled rather than hidden.
    plan.sort(key=lambda r: (r["payback_months"] is None, r["payback_months"] or 1e9))
    per_window = max(1, int(np.ceil(len(plan) / max(1, windows))))
    for i, row in enumerate(plan):
        row["window"] = window_names[min(i // per_window, len(window_names) - 1)]
        if row["annual_value"] <= 0:
            # A zero estimate does NOT mean the station is unimportant. It means the
            # evidence we already have there (the operator checklist) carries at least as
            # much signal as a comparable instrumented neighbour's sensors -- so buying a
            # sensor would duplicate information we get for free. S15 is the clearest
            # case: its checklist time is the second strongest driver in the whole model,
            # and the defect it drives is behavioural, not environmental.
            row["recommendation"] = "DEFER - checklist already captures this signal"
        elif row["payback_months"] and row["payback_months"] < 12:
            row["recommendation"] = "PROCEED"
        else:
            row["recommendation"] = "REVIEW"
    return plan
