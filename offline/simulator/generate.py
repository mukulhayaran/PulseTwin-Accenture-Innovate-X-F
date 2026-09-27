"""Simulator CLI.

    python -m offline.simulator.generate --config configs/plant_a.yaml

Writes to data/<plant_id>/:
    telemetry.parquet    one row per unit per station: timings, states, sensor readings
    genealogy.parquet    unit journey: entry/exit per station, operator, shift, lot
    checklists.parquet   manual-station records: duration, pass, notes code
    inspections.parquet  per unit: defect_detected (noisy label), true_defect (hidden)

Determinism: every random stream is spawned from a single SeedSequence built from the
config's seed, so a rerun reproduces byte-identical output. OMP_NUM_THREADS is pinned in
the Makefile for the same reason (ADR-016).
"""

from __future__ import annotations

import argparse
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from offline.config_schema import PlantConfig, load_plant_config
from offline.simulator.causal_engine import build_unit_signals, compute_defects
from offline.simulator.inspection import inspect
from offline.simulator.line_model import (
    ALL_STATES,
    SIM_START,
    STATE_BUCKET_S,
    LineSimulator,
    ground_truth_bottleneck,
)
from offline.simulator.operators import OperatorPool
from offline.simulator.physics import (
    CONTINUOUS_SIGNALS,
    autocorrelation,
    build_latents,
    generate_signals,
)

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

VISIT_COLUMNS = [
    "unit_seq",
    "station_id",
    "entry_ts",
    "exit_ts",
    "cycle_s",
    "prior_state",
    "blocked_before_s",
    "buffer_wait_s",
    "checklist_s",
    "operator_id",
    "shift_id",
]


def _to_datetime(seconds: np.ndarray) -> pd.Series:
    return pd.to_datetime(SIM_START) + pd.to_timedelta(seconds, unit="s")


def run(cfg: PlantConfig, out_root: Path = DATA_ROOT) -> dict:
    t_start = time.time()
    root = np.random.SeedSequence(cfg.simulation.seed)
    rng_pool, rng_des, rng_latent, rng_phys, rng_causal, rng_insp = [
        np.random.default_rng(s) for s in root.spawn(6)
    ]

    # ------------------------------------------------------------------ 1. the line
    pool = OperatorPool(cfg, rng_pool)
    result = LineSimulator(cfg, pool, rng_des).run()

    visits = pd.DataFrame(result.visits, columns=VISIT_COLUMNS)
    units = pd.DataFrame(
        [(u.seq, u.unit_id, u.variant, u.lot_id, u.released_at) for u in result.units],
        columns=["unit_seq", "unit_id", "variant", "lot_id", "released_at"],
    )
    visits = visits.merge(units[["unit_seq", "unit_id", "variant", "lot_id"]], on="unit_seq")

    # Keep only units that completed the whole line -- a partial journey has no label.
    last_station = max(cfg.station_ids)
    complete = set(visits.loc[visits["station_id"] == last_station, "unit_seq"])
    warm = cfg.simulation.warmup_units
    keep = {s for s in complete if s >= warm}
    visits = visits[visits["unit_seq"].isin(keep)].reset_index(drop=True)
    units = units[units["unit_seq"].isin(keep)].reset_index(drop=True)

    # ------------------------------------------------------------- 2. latent causes
    n_seq = int(visits["unit_seq"].max()) + 1
    latents = build_latents(cfg, n_seq, rng_latent)

    # ------------------------------------------------------------------ 3. signals
    telemetry = generate_signals(cfg, visits, latents, pool.torque_bias_lookup(), rng_phys)

    # ------------------------------------------------------------------ 4. defects
    signals = build_unit_signals(cfg, telemetry, latents)
    causal = compute_defects(cfg, signals, rng_causal)
    insp = inspect(cfg, causal.true_defect, rng_insp)

    # ------------------------------------------------------------------- 5. persist
    out = out_root / cfg.plant_id
    out.mkdir(parents=True, exist_ok=True)

    telemetry_out = telemetry.copy()
    telemetry_out["timestamp"] = _to_datetime(telemetry_out["entry_ts"].to_numpy())
    telemetry_out["plant_id"] = cfg.plant_id
    telemetry_out.to_parquet(out / "telemetry.parquet", index=False)

    genealogy = telemetry[
        ["unit_seq", "unit_id", "station_id", "entry_ts", "exit_ts", "cycle_s",
         "prior_state", "blocked_before_s", "buffer_wait_s", "variant", "lot_id",
         "operator_id", "shift_id"]
    ].copy()
    genealogy["entry_time"] = _to_datetime(genealogy["entry_ts"].to_numpy())
    genealogy["exit_time"] = _to_datetime(genealogy["exit_ts"].to_numpy())
    genealogy["plant_id"] = cfg.plant_id
    genealogy.to_parquet(out / "genealogy.parquet", index=False)

    checklists = pd.DataFrame(
        pool.checklists,
        columns=["unit_seq", "station_id", "ts", "operator_id", "checklist_pass",
                 "checklist_time_s", "notes_code", "was_rushed"],
    )
    checklists = checklists[checklists["unit_seq"].isin(keep)].reset_index(drop=True)
    checklists = checklists.merge(units[["unit_seq", "unit_id"]], on="unit_seq")
    checklists["timestamp"] = _to_datetime(checklists["ts"].to_numpy())
    checklists["plant_id"] = cfg.plant_id
    checklists.to_parquet(out / "checklists.parquet", index=False)

    end_ts = telemetry.groupby("unit_seq")["exit_ts"].max()
    inspections = pd.DataFrame(
        {
            "unit_seq": signals["unit_seq"].to_numpy(),
            "defect_detected": insp.defect_detected,
            "true_defect": insp.true_defect,
            "escaped": insp.escaped,
            "false_reject": insp.false_reject,
            "defect_mode": causal.defect_mode,
            "propensity": causal.propensity,
        }
    )
    inspections = inspections.merge(units[["unit_seq", "unit_id", "variant", "lot_id"]], on="unit_seq")
    inspections["inspection_ts"] = end_ts.reindex(inspections["unit_seq"]).to_numpy()
    inspections["inspection_time"] = _to_datetime(inspections["inspection_ts"].to_numpy())
    inspections["plant_id"] = cfg.plant_id
    inspections.to_parquet(out / "inspections.parquet", index=False)

    # Observable failure conditions per unit (no latent terms). Read only by
    # offline/experiments/p75_prototype.py; nothing in the artifact build touches it.
    signals[["unit_seq", "obs_mode_a", "signal_mode_b", "signal_mode_c"]].to_parquet(
        out / "risk_conditions.parquet", index=False)

    np.savez_compressed(
        out / "state_buckets.npz",
        bucket_s=STATE_BUCKET_S,
        states=np.array(ALL_STATES),
        station_ids=np.array(cfg.station_ids),
        seconds=np.stack([result.state_seconds[s] for s in cfg.station_ids]),
        ground_truth_bottleneck=ground_truth_bottleneck(result, cfg),
    )

    # ---------------------------------------------------------------- diagnostics
    diag = _diagnostics(cfg, result, telemetry, checklists, insp, causal, time.time() - t_start)
    return diag


def _diagnostics(cfg, result, telemetry, checklists, insp, causal, elapsed) -> dict:
    dp = cfg.defect_physics
    manual_ids = [s.id for s in cfg.manual_stations()]

    manual_rows = telemetry[telemetry["station_id"].isin(manual_ids)]
    manual_nan = float(manual_rows[list(CONTINUOUS_SIGNALS)].isna().all(axis=1).mean())

    acs = {}
    for sig, st in (("torque_nm", dp.mode_c.contributing_stations[0]),
                    ("vibration_mm_s", dp.mode_c.contributing_stations[1]),
                    ("temperature_c", dp.mode_a.contributing_stations[0])):
        col = telemetry.loc[telemetry["station_id"] == st].sort_values("entry_ts", kind="stable")[sig]
        acs[sig] = autocorrelation(col.to_numpy())

    truth = ground_truth_bottleneck(result, cfg)
    wd = np.array(
        [(SIM_START + timedelta(seconds=b * STATE_BUCKET_S)).weekday() for b in range(len(truth))]
    )
    peak = set(cfg.ambient.humidity_high_days)
    pk = truth[np.isin(wd, list(peak)) & (truth > 0)]
    nm = truth[~np.isin(wd, list(peak)) & (truth > 0)]

    iw, ib, isv = (ALL_STATES.index(x) for x in ("working", "blocked", "starved"))
    blocked_h = sum(result.state_seconds[s.id][:, ib].sum() for s in cfg.stations) / 3600
    starved_h = sum(result.state_seconds[s.id][:, isv].sum() for s in cfg.stations) / 3600

    mode_counts = pd.Series(causal.defect_mode)
    mode_counts = mode_counts[mode_counts != ""].value_counts().to_dict()

    rush = checklists[checklists["was_rushed"]]
    b_st = dp.mode_b.contributing_station
    b_rows = checklists[checklists["station_id"] == b_st]

    return {
        "plant_id": cfg.plant_id,
        "elapsed_s": elapsed,
        "units": int(len(insp.true_defect)),
        "achieved_uph": result.achieved_uph(),
        "target_uph": cfg.units_per_hour_target,
        "uph_pct_of_target": 100.0 * result.achieved_uph() / cfg.units_per_hour_target,
        "manual_all_nan_frac": manual_nan,
        "autocorrelation": acs,
        "bottleneck_normal": (int(np.bincount(nm).argmax()) if len(nm) else -1),
        "bottleneck_peak": (int(np.bincount(pk).argmax()) if len(pk) else -1),
        "bottleneck_peak_share": float((pk == np.bincount(pk).argmax()).mean()) if len(pk) else 0.0,
        "blocked_hours": blocked_h,
        "starved_hours": starved_h,
        "rush_rate_at_mode_b_station": float(len(rush) / max(1, len(b_rows))),
        "rush_on_peak_days_frac": float(
            np.isin([(SIM_START + timedelta(seconds=t)).weekday() for t in rush["ts"]], list(peak)).mean()
        ) if len(rush) else 0.0,
        "fitted_bias": causal.fitted_bias,
        "true_defect_rate": float(insp.true_defect.mean()),
        "defect_modes": mode_counts,
        **insp.summary(),
    }


def print_diagnostics(d: dict) -> bool:
    """Print the Phase 2 acceptance checks. Returns True if all pass."""
    ok = True

    def check(label: str, value: str, passed: bool) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {label:<44} {value}")

    print(f"\n=== {d['plant_id']} =========================================")
    print(f"  runtime {d['elapsed_s']:.1f}s   units {d['units']:,}")
    check("throughput within 10% of target",
          f"{d['achieved_uph']:.2f} UPH ({d['uph_pct_of_target']:.1f}%)",
          abs(d["uph_pct_of_target"] - 100) <= 10)
    check("realised defect rate in 2.5-4.0%",
          f"{d['true_defect_rate']:.2%}",
          0.025 <= d["true_defect_rate"] <= 0.040)
    check("positive-label contamination < 10%",
          f"{d['false_positive_share']:.1%} of {d['positive_labels']} labels",
          d["false_positive_share"] < 0.10)
    check("manual stations 100% NaN on continuous sensors",
          f"{d['manual_all_nan_frac']:.1%}",
          d["manual_all_nan_frac"] > 0.999)
    for sig, v in d["autocorrelation"].items():
        check(f"autocorrelation > 0.3  ({sig})", f"{v:.3f}", v > 0.30)
    check("bottleneck shifts between normal and peak days",
          f"S{d['bottleneck_normal']} -> S{d['bottleneck_peak']} ({d['bottleneck_peak_share']:.0%})",
          d["bottleneck_normal"] != d["bottleneck_peak"])
    check("non-zero blocked AND starved time",
          f"blocked {d['blocked_hours']:.0f}h  starved {d['starved_hours']:.0f}h",
          d["blocked_hours"] > 0 and d["starved_hours"] > 0)
    check("both Tuesday defect modes present",
          ", ".join(f"{k}={v}" for k, v in sorted(d["defect_modes"].items())),
          d["defect_modes"].get("mode_a", 0) > 20 and d["defect_modes"].get("mode_b", 0) > 20)
    check("Mode B rush concentrated on peak days",
          f"{d['rush_rate_at_mode_b_station']:.1%} of visits, "
          f"{d['rush_on_peak_days_frac']:.0%} on peak days",
          d["rush_on_peak_days_frac"] > 0.5)
    print(f"  escape rate {d['escape_rate']:.1%}   fitted bias {d['fitted_bias']:.3f}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate simulated plant data")
    ap.add_argument("--config", required=True)
    ap.add_argument("--seed", type=int, default=None, help="override the config seed")
    ap.add_argument("--out", default=str(DATA_ROOT))
    args = ap.parse_args()

    cfg = load_plant_config(args.config)
    if args.seed is not None:
        cfg.simulation.seed = args.seed

    diag = run(cfg, Path(args.out))
    return 0 if print_diagnostics(diag) else 1


if __name__ == "__main__":
    raise SystemExit(main())
