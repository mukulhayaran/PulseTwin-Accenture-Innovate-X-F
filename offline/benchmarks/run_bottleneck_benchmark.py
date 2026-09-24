"""Score every bottleneck detector against throughput-sensitivity ground truth.

    python -m offline.benchmarks.run_bottleneck_benchmark --plant plant_a
    python -m offline.benchmarks.run_bottleneck_benchmark --plant plant_b --profile full

Writes three machine-readable artifacts (merged across invocations, so the two plants can
be run separately):

    artifacts/benchmarks/bottleneck_ground_truth.csv      per station, per scenario
    artifacts/benchmarks/bottleneck_detector_results.csv  per detector, per scenario
    artifacts/benchmarks/bottleneck_summary.json          headline numbers + provenance

COST. One scenario costs `n_seeds * (n_stations + 2)` line runs. Plant A at the `dev`
profile is 960 runs of roughly 3 seconds. This is an offline experiment: it is never
invoked by the API, by the build, or at startup.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from offline.benchmarks import bottleneck_ground_truth as gt
from offline.benchmarks.bottleneck_detectors import DETECTORS, observe, ranking
from offline.benchmarks.simrun import Scenario, run_line, scenarios_for, with_horizon
from offline.config_schema import load_plant_config

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "artifacts" / "benchmarks"

PROFILES = {
    # 30 seeds is the brief's floor for a normal development run.
    "dev": {"seeds": 30, "days": 5.0},
    # 60+ seeds and a fortnight of line time for a publication-quality claim.
    "full": {"seeds": 60, "days": 14.0},
    # Smoke only. Never cite a number produced at this profile.
    "smoke": {"seeds": 3, "days": 1.0},
}

SCHEMA_VERSION = 1


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


# ------------------------------------------------------------------------------ scoring


def score_ranking(order: list[int], truth: int, station_ids: list[int]) -> dict:
    """Score one detector's ranking for one seed against the true constraint.

    `station_distance` is the gap in PHYSICAL POSITION along the line between the
    detector's first choice and the true constraint -- meaningful here because both
    plants are serial lines, so "wrong by one station" and "wrong by twenty" are very
    different errors. It would need redefining under the topology abstraction for a line
    with parallel branches, and docs/BOTTLENECK_BENCHMARK.md says so.
    """
    position = {sid: i for i, sid in enumerate(station_ids)}
    rank = order.index(truth)
    return {
        "top1": 1.0 if order[0] == truth else 0.0,
        "top3": 1.0 if truth in order[:3] else 0.0,
        "reciprocal_rank": 1.0 / (rank + 1),
        "station_distance": float(abs(position[order[0]] - position[truth])),
    }


def _mean_ci(values: list[float], seed: int = 42) -> tuple[float, float, float]:
    arr = np.asarray(values, dtype=np.float64)
    low, high = gt.bootstrap_ci(arr, seed=seed)
    return float(arr.mean()), low, high


# --------------------------------------------------------------------------- experiment


def run_scenario(
    scenario: Scenario,
    base_cfg,
    seeds: list[int],
    days: float,
    relative: float,
    workers: int,
) -> tuple[gt.GroundTruth, list[dict], list[dict]]:
    """Ground truth + detector scores for one scenario. Returns (gt, gt_rows, det_rows)."""
    cfg = scenario.apply(with_horizon(base_cfg, days))

    truth = gt.compute(
        cfg, seeds, relative=relative, workers=workers, scenario=scenario.key, verbose=True
    )

    gt_rows = []
    for station in sorted(truth.stations, key=lambda s: s.mean):
        row = station.to_row()
        row.update(
            plant_id=cfg.plant_id,
            scenario=scenario.key,
            is_ground_truth_bottleneck=int(station.station_id == truth.bottleneck_station),
            relative_perturbation=relative,
            horizon_days=days,
        )
        gt_rows.append(row)

    # Detector evaluation runs the UNPERTURBED scenario config once per seed. A detector
    # only ever sees the line as it is; the perturbations exist to establish truth, and a
    # detector that got to see them would be cheating.
    print(f"[detectors] {cfg.plant_id}/{scenario.key}: {len(seeds)} instrumented runs", flush=True)
    per_seed: dict[str, list[dict]] = {d.key: [] for d in DETECTORS}
    for i, seed in enumerate(seeds, 1):
        obs = observe(run_line(cfg, seed, instrument=True), cfg)
        for detector in DETECTORS:
            order = ranking(detector.fn(obs))
            per_seed[detector.key].append(
                score_ranking(order, truth.bottleneck_station, obs.station_ids)
            )
        if i % 10 == 0 or i == len(seeds):
            print(f"  detectors: {i}/{len(seeds)}", flush=True)

    det_rows = []
    for detector in DETECTORS:
        scores = per_seed[detector.key]
        row = {
            "plant_id": cfg.plant_id,
            "scenario": scenario.key,
            "detector": detector.key,
            "detector_label": detector.label,
            "provenance": detector.provenance,
            "is_current_detector": int(detector.is_current),
            "n_seeds": len(seeds),
            "ground_truth_station": truth.bottleneck_station,
            "ground_truth_separated": int(truth.separated),
        }
        for metric in ("top1", "top3", "reciprocal_rank", "station_distance"):
            mean, low, high = _mean_ci([s[metric] for s in scores])
            row[metric] = mean
            row[f"{metric}_ci_low"] = low
            row[f"{metric}_ci_high"] = high
        det_rows.append(row)

    return truth, gt_rows, det_rows


# ------------------------------------------------------------------------------ artifacts


def _merge_csv(path: Path, rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Replace rows matching the incoming (plant, scenario, ...) keys; keep the rest.

    Lets `--plant plant_a` and `--plant plant_b` be run as separate jobs -- or a single
    scenario be re-run after a fix -- without discarding the other results.
    """
    existing: list[dict] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as fh:
            existing = list(csv.DictReader(fh))
    incoming_keys = {tuple(str(r[k]) for k in keys) for r in rows}
    kept = [r for r in existing if tuple(str(r.get(k)) for k in keys) not in incoming_keys]
    merged = kept + [{k: v for k, v in r.items()} for r in rows]
    if not merged:
        return merged
    fieldnames = list(rows[0].keys())
    for row in merged:
        for name in row:
            if name not in fieldnames:
                fieldnames.append(name)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in merged:
            writer.writerow(row)
    return merged


def write_artifacts(
    plant_id: str,
    truths: list[gt.GroundTruth],
    gt_rows: list[dict],
    det_rows: list[dict],
    profile: str,
    relative: float,
    elapsed_s: float,
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _merge_csv(
        OUT_DIR / "bottleneck_ground_truth.csv", gt_rows, ("plant_id", "scenario", "station_id")
    )
    merged_det = _merge_csv(
        OUT_DIR / "bottleneck_detector_results.csv",
        det_rows,
        ("plant_id", "scenario", "detector"),
    )

    summary_path = OUT_DIR / "bottleneck_summary.json"
    summary = {}
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    summary.setdefault("schema_version", SCHEMA_VERSION)
    summary["generated_by"] = "offline/benchmarks/run_bottleneck_benchmark.py"
    summary.setdefault("runs", {})
    summary["runs"][plant_id] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "profile": profile,
        "n_seeds": PROFILES[profile]["seeds"],
        "horizon_days": PROFILES[profile]["days"],
        "relative_perturbation": relative,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "elapsed_s": round(elapsed_s, 1),
        "scenarios": {t.scenario: t.to_summary() for t in truths},
    }

    # Cross-plant detector league table, aggregated over every scenario recorded so far.
    by_detector: dict[str, dict] = {}
    for row in merged_det:
        key = row["detector"]
        entry = by_detector.setdefault(
            key,
            {
                "detector": key,
                "label": row["detector_label"],
                "provenance": row["provenance"],
                "is_current_detector": int(row["is_current_detector"]),
                "scenarios": 0,
                "_top1": [],
                "_top3": [],
                "_mrr": [],
                "_dist": [],
            },
        )
        entry["scenarios"] += 1
        entry["_top1"].append(float(row["top1"]))
        entry["_top3"].append(float(row["top3"]))
        entry["_mrr"].append(float(row["reciprocal_rank"]))
        entry["_dist"].append(float(row["station_distance"]))

    league = []
    for entry in by_detector.values():
        league.append(
            {
                "detector": entry["detector"],
                "label": entry["label"],
                "provenance": entry["provenance"],
                "is_current_detector": entry["is_current_detector"],
                "scenarios": entry["scenarios"],
                "top1": float(np.mean(entry["_top1"])),
                "top3": float(np.mean(entry["_top3"])),
                "mrr": float(np.mean(entry["_mrr"])),
                "mean_station_distance": float(np.mean(entry["_dist"])),
            }
        )
    league.sort(key=lambda e: (-e["top1"], -e["mrr"]))
    summary["league_table"] = league
    summary["league_table_note"] = (
        "Macro-average over every (plant, scenario) recorded in "
        "bottleneck_detector_results.csv, weighting each scenario equally. Scenarios "
        "whose ground truth is not separated (the deliberately ambiguous ones) are "
        "INCLUDED here; per-scenario rows carry ground_truth_separated so they can be "
        "excluded."
    )

    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plant", default="plant_a", choices=("plant_a", "plant_b"))
    parser.add_argument("--profile", default="dev", choices=tuple(PROFILES))
    parser.add_argument("--scenario", default=None, help="run only this scenario key")
    parser.add_argument("--relative", type=float, default=gt.DEFAULT_RELATIVE,
                        help="perturbation size, e.g. 0.10 or 0.15")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--seeds", type=int, default=None, help="override the profile's seed count")
    parser.add_argument("--days", type=float, default=None, help="override the profile's horizon")
    args = parser.parse_args(argv)

    profile = PROFILES[args.profile]
    n_seeds = args.seeds if args.seeds is not None else profile["seeds"]
    days = args.days if args.days is not None else profile["days"]
    seeds = gt.seed_list(n_seeds)

    base_cfg = load_plant_config(str(ROOT / "configs" / f"{args.plant}.yaml"))
    scenarios = scenarios_for(args.plant)
    if args.scenario:
        scenarios = [s for s in scenarios if s.key == args.scenario]
        if not scenarios:
            parser.error(f"no scenario '{args.scenario}' for {args.plant}")

    print(
        f"=== bottleneck benchmark: {args.plant} profile={args.profile} "
        f"seeds={n_seeds} days={days} perturbation=+{args.relative:.0%} "
        f"workers={args.workers} scenarios={[s.key for s in scenarios]}",
        flush=True,
    )

    started = time.time()
    truths, gt_rows, det_rows = [], [], []
    for scenario in scenarios:
        truth, g_rows, d_rows = run_scenario(
            scenario, base_cfg, seeds, days, args.relative, args.workers
        )
        truths.append(truth)
        gt_rows.extend(g_rows)
        det_rows.extend(d_rows)
        # Checkpoint after every scenario: a full-profile plant B run is over an hour and
        # losing all of it to a failure in the last scenario would be its own bug.
        write_artifacts(
            args.plant, truths, g_rows, d_rows, args.profile, args.relative,
            time.time() - started,
        )
        flag = "" if truth.separated else "  [NOT SEPARATED -- ambiguous by design]"
        print(
            f"--- {args.plant}/{scenario.key}: ground truth = S{truth.bottleneck_station}"
            f" (runner-up S{truth.runner_up_station}, margin {truth.margin:.1f}){flag}",
            flush=True,
        )
        for row in sorted(d_rows, key=lambda r: -r["top1"]):
            print(
                f"      {row['detector']:32s} top1={row['top1']:.2f} "
                f"top3={row['top3']:.2f} mrr={row['reciprocal_rank']:.3f} "
                f"dist={row['station_distance']:.1f}",
                flush=True,
            )

    print(f"=== wrote artifacts to {OUT_DIR} in {time.time() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
