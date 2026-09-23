"""Benchmark CLI.

    python -m benchmark.run --dataset ai4i --task predictive_maintenance
    python -m benchmark.run --dataset bosch --task defect_prediction [--sample-size N] [--full]
    python -m benchmark.run --dataset bosch --task sensor_robustness
    python -m benchmark.run --rca-eval [--n-units N]
    python -m benchmark.run --all

Caching (§28 of the source brief): re-running the same dataset/task does not re-download
or re-convert raw data -- `DatasetAdapter.download()` and `.load()` are already
idempotent by construction. `--force` is accepted for symmetry with the rest of the repo's
CLI conventions but currently only affects the raw-download step, since training itself is
fast enough here not to need its own cache.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")  # ADR-016 convention, before sklearn import

from benchmark import masking, registry, train as bt
from benchmark.report import build_report, write_report
from offline.ml.defect_model import DEFAULT_HYPERPARAMS


def _run_defect_prediction(dataset_id: str, sample_size: int | None, *, seed: int = 42) -> str:
    adapter = registry.adapter_for(dataset_id)
    adapter.download()
    df = adapter.load(sample_size=sample_size)
    tr, va, te = adapter.create_train_test_split(df, strategy="stratified", seed=seed)

    Xtr, ytr = adapter.feature_matrix(tr)
    Xva, yva = adapter.feature_matrix(va)
    Xte, yte = adapter.feature_matrix(te)

    fs = bt.build_feature_set(Xtr, ytr, Xva, yva, Xte, yte)
    model, importance, threshold_result = bt.train_and_evaluate(fs, seed=seed)

    run_id = secrets.token_hex(6)
    report = build_report(
        run_id=run_id, config=adapter.config, task="defect_prediction",
        split_strategy="stratified (seeded, class-balanced)",
        n_train=len(tr), n_validate=len(va), n_test=len(te), n_features=Xtr.shape[1],
        target_prevalence=float(df[adapter.get_target()].mean()),
        model_name="HistGradientBoostingClassifier + Platt calibration",
        hyperparameters=DEFAULT_HYPERPARAMS,
        metrics=model.metrics, threshold_metrics=threshold_result,
        feature_importance=importance,
    )
    json_path, md_path = write_report(report)
    print(f"[benchmark] {dataset_id}/defect_prediction run={run_id}")
    print(f"  PR-AUC {model.metrics['pr_auc']:.4f} "
         f"[{model.metrics['pr_auc_ci_low']:.4f}, {model.metrics['pr_auc_ci_high']:.4f}]  "
         f"F1 {threshold_result['f1']:.4f}  precision {threshold_result['precision']:.4f}  "
         f"recall {threshold_result['recall']:.4f}")
    print(f"  report: {json_path}")
    print(f"          {md_path}")
    return run_id


def _run_sensor_robustness(dataset_id: str, sample_size: int | None, *, seed: int = 42) -> str:
    adapter = registry.adapter_for(dataset_id)
    adapter.download()
    df = adapter.load(sample_size=sample_size)
    tr, va, te = adapter.create_train_test_split(df, strategy="stratified", seed=seed)
    Xtr, ytr = adapter.feature_matrix(tr)
    Xva, yva = adapter.feature_matrix(va)
    Xte, yte = adapter.feature_matrix(te)

    # STATION_LEVEL only where the adapter can say which station a column belongs to
    # (Bosch; AI4I has no line/station structure at all -- see its DatasetConfig
    # limitations). Checked by capability (hasattr), not by dataset_id, so a future
    # adapter gets STATION_LEVEL for free the moment it implements station_of().
    strategies = [masking.RANDOM]
    station_of = None
    if hasattr(adapter, "station_of"):
        station_of = adapter.station_of(tr)
        if station_of:
            strategies.append(masking.STATION_LEVEL)

    rows = []
    last_metrics = last_threshold = None
    for strategy in strategies:
        for coverage in masking.COVERAGE_LEVELS:
            kwargs = {"station_of": station_of} if strategy == masking.STATION_LEVEL else {}
            Xtr_m = masking.mask(Xtr, coverage, strategy, seed=seed, **kwargs)
            Xva_m = masking.mask(Xva, coverage, strategy, seed=seed, **kwargs)
            Xte_m = masking.mask(Xte, coverage, strategy, seed=seed, **kwargs)
            fs = bt.build_feature_set(Xtr_m, ytr, Xva_m, yva, Xte_m, yte)
            model, _importance, threshold_result = bt.train_and_evaluate(fs, seed=seed)
            rows.append({
                "coverage_pct": coverage, "strategy": strategy,
                "pr_auc": model.metrics["pr_auc"], "f1": threshold_result["f1"],
            })
            print(f"  [{strategy:13s}] coverage {coverage:3d}%  "
                 f"PR-AUC {model.metrics['pr_auc']:.4f}  F1 {threshold_result['f1']:.4f}")
            last_metrics, last_threshold = model.metrics, threshold_result

    run_id = secrets.token_hex(6)
    report = build_report(
        run_id=run_id, config=adapter.config, task="sensor_robustness",
        split_strategy="stratified (seeded, class-balanced)",
        n_train=len(tr), n_validate=len(va), n_test=len(te), n_features=Xtr.shape[1],
        target_prevalence=float(df[adapter.get_target()].mean()),
        model_name="HistGradientBoostingClassifier + Platt calibration",
        hyperparameters=DEFAULT_HYPERPARAMS, metrics=last_metrics,
        threshold_metrics=last_threshold,
        feature_importance=[], sensor_robustness=rows,
    )
    json_path, md_path = write_report(report)
    print(f"[benchmark] {dataset_id}/sensor_robustness run={run_id}")
    print(f"  report: {json_path}")
    return run_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PulseTwin real-dataset benchmark runner.")
    p.add_argument("--dataset", choices=sorted(registry.REGISTRY))
    p.add_argument("--task", default="defect_prediction",
                   choices=["defect_prediction", "predictive_maintenance", "sensor_robustness"])
    p.add_argument("--sample-size", type=int, default=None)
    p.add_argument("--full", action="store_true", help="ignore --sample-size, load everything")
    p.add_argument("--rca-eval", action="store_true")
    p.add_argument("--n-units", type=int, default=20, help="units sampled for --rca-eval")
    p.add_argument("--all", action="store_true")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    sample_size = None if args.full else args.sample_size

    if args.rca_eval:
        from benchmark.rca_eval import run_rca_eval
        run_rca_eval(n_units=args.n_units, seed=args.seed)
        return 0

    if args.all:
        from benchmark.adapters.base import ManualDownloadRequired
        failures = []
        for dataset_id in sorted(registry.REGISTRY):
            try:
                _run_defect_prediction(dataset_id, sample_size, seed=args.seed)
            except ManualDownloadRequired as exc:
                # Expected for Bosch without a manually-placed file -- report and move
                # on to the next dataset rather than crashing the whole --all run over
                # one dataset's known, documented prerequisite.
                print(f"[benchmark] {dataset_id}: skipped -- {exc}")
                failures.append(dataset_id)
        if failures:
            print(f"[benchmark] --all finished; skipped (manual download required): "
                 f"{', '.join(failures)}")
        return 0

    if not args.dataset:
        p.error("--dataset is required unless --all or --rca-eval is given")

    if args.task == "sensor_robustness":
        _run_sensor_robustness(args.dataset, sample_size, seed=args.seed)
    else:
        _run_defect_prediction(args.dataset, sample_size, seed=args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
