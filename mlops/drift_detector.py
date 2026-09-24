"""Drift detection: per-feature PSI, calibration drift, and sensor-coverage drift.

    python -m mlops.drift_detector --config configs/plant_a.yaml

Writes to the `drift_metrics` table (migrations/004) through serve.db's dual-backend
dispatch (serve.db.open_ops_only()), so serve/routers/model_health.py reads exactly what
this writes regardless of whether Postgres or the SQLite fallback is active.

REFERENCE vs CURRENT, stated honestly: this system replays a fixed simulated dataset, not
a live incoming stream, so "current" here is the model's own TEST split -- the population
it is actually scored against, per offline/ml/train.py's temporal split -- and "reference"
is the TRAIN split it was fitted on. That is a real, answerable drift question ("does
serving-time data still look like training-time data?"), not a placeholder computed for
its own sake. docs/MLOPS_LIFECYCLE.md documents what changes once a genuinely live feed
exists: `current` becomes a real rolling window sourced from ingestion/'s station_readings
instead of a parquet split.

mlops/ is offline-tier code (CLAUDE.md SS2 exempts it from the import wall, same as
offline/ml/*) and may use pandas.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from offline.config_schema import load_plant_config
from serve import db

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

ROLLING_WINDOW_UNITS = 500
PSI_WARN, PSI_ALERT = 0.1, 0.2
CALIBRATION_ALERT_PP = 15.0
TOP_N_FEATURES = 20
COVERAGE_FULL_MAX_NAN = 0.05     # <=5% of feature columns missing
COVERAGE_PARTIAL_MAX_NAN = 0.40  # 5-40% missing = partial; above that = manual-only


def compute_psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index. PSI<0.1 no shift, 0.1-0.2 moderate, >0.2 significant.

    Bin edges come from the REFERENCE distribution's own quantiles (standard PSI
    construction) so the comparison is "does current data fall into the same buckets
    reference data defined," not two independently-binned histograms.
    """
    reference = reference[~np.isnan(reference)]
    current = current[~np.isnan(current)]
    if len(reference) < bins or len(current) == 0:
        return 0.0
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges = edges.copy()
    edges[0], edges[-1] = -np.inf, np.inf
    ref_counts, _ = np.histogram(reference, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)
    # Laplace-style floor avoids log(0)/division-by-zero on an empty bin without letting
    # one sparse bin dominate the sum.
    ref_pct = np.clip(ref_counts / max(1, ref_counts.sum()), 1e-4, None)
    cur_pct = np.clip(cur_counts / max(1, cur_counts.sum()), 1e-4, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def calibration_drift(predicted: np.ndarray, actual: np.ndarray) -> dict:
    """Fixed deciles [0.0-0.1) .. [0.9-1.0]. Per finals brief: flag if the max gap between
    mean predicted probability and observed defect rate in any bin exceeds 15pp."""
    edges = np.linspace(0.0, 1.0, 11)
    max_dev = 0.0
    per_bin = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (predicted >= lo) & (predicted < hi if hi < 1.0 else predicted <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        mean_pred = float(predicted[mask].mean())
        observed = float(actual[mask].mean())
        dev = abs(mean_pred - observed) * 100.0
        max_dev = max(max_dev, dev)
        per_bin.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": n, "predicted": round(mean_pred, 4),
                         "observed": round(observed, 4), "deviation_pp": round(dev, 2)})
    return {"max_deviation_pp": round(max_dev, 2), "bins": per_bin}


def sensor_coverage(features: pd.DataFrame) -> dict:
    """Per-unit coverage tier from the fraction of feature columns that are NaN --
    the artifact's own signal for "how much of this unit's journey was actually
    instrumented" (a manual station or a dropped sensor leaves its derived features NaN;
    see offline/ml/featurize.py). No separate per-unit sensor-tier field exists, so this
    is the honest proxy computable from what was already built.
    """
    nan_frac = features.isna().mean(axis=1)
    full = float((nan_frac <= COVERAGE_FULL_MAX_NAN).mean())
    partial = float(((nan_frac > COVERAGE_FULL_MAX_NAN) &
                      (nan_frac <= COVERAGE_PARTIAL_MAX_NAN)).mean())
    manual = float((nan_frac > COVERAGE_PARTIAL_MAX_NAN).mean())
    return {"full": round(full, 4), "partial": round(partial, 4), "manual": round(manual, 4)}


def run(config_path: str, model_id: str | None = None) -> dict:
    cfg = load_plant_config(config_path)
    pid = cfg.plant_id
    d = DATA / pid

    feats = pd.read_parquet(d / "features.parquet")
    meta = pd.read_parquet(d / "feature_meta.parquet")
    scores = pd.read_parquet(d / "scores.parquet").set_index("unit_seq")
    metrics = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    model_id = model_id or f"{pid}_champion"

    # features.parquet is indexed by unit_seq; feature_meta.parquet is not (written with
    # index=False) -- key everything off the `unit_seq` COLUMN, never off either frame's
    # positional index.
    train_idx = meta.loc[meta["split"] == "train", "unit_seq"].to_numpy()
    test_meta = meta.loc[meta["split"] == "test", ["unit_seq", "prediction_ts"]]
    # "Current" = the most recent `ROLLING_WINDOW_UNITS` of the test split, ordered by
    # prediction_ts -- the freshest slice of the population the model is actually scored
    # against, mirroring how a real rolling window would fill in production.
    current_idx = test_meta.sort_values("prediction_ts")["unit_seq"].to_numpy()[-ROLLING_WINDOW_UNITS:]

    rows_to_write: list[dict] = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------- per-feature PSI
    top_features = [r["feature"] for r in metrics["permutation_importance"][:TOP_N_FEATURES]]
    n_shifted, worst = 0, None
    for feat in top_features:
        if feat not in feats.columns:
            continue
        ref = feats.loc[train_idx, feat].to_numpy(dtype=np.float64)
        cur = feats.loc[current_idx, feat].to_numpy(dtype=np.float64)
        psi = compute_psi(ref, cur)
        status = "alert" if psi > PSI_ALERT else ("warning" if psi > PSI_WARN else "healthy")
        if status != "healthy":
            n_shifted += 1
        if worst is None or psi > worst["psi"]:
            worst = {"name": feat, "psi": psi, "status": status}
        rows_to_write.append({
            "metric_type": "psi", "feature_name": feat, "value": psi, "threshold": PSI_ALERT,
            "alert_triggered": psi > PSI_ALERT, "window_size": len(current_idx),
        })

    # ---------------------------------------------------------------- calibration
    y_cur = scores.loc[current_idx, "y"].to_numpy(dtype=np.float64)
    p_cur = scores.loc[current_idx, "calibrated_risk"].to_numpy(dtype=np.float64)
    cal = calibration_drift(p_cur, y_cur)
    cal_status = "alert" if cal["max_deviation_pp"] > CALIBRATION_ALERT_PP else "healthy"
    rows_to_write.append({
        "metric_type": "calibration_drift", "feature_name": None,
        "value": cal["max_deviation_pp"], "threshold": CALIBRATION_ALERT_PP,
        "alert_triggered": cal_status == "alert", "window_size": len(current_idx),
    })

    # ------------------------------------------------------------ sensor coverage
    train_cov = sensor_coverage(feats.loc[train_idx])
    cur_cov = sensor_coverage(feats.loc[current_idx])
    coverage_drop = train_cov["full"] - cur_cov["full"]
    coverage_status = "within_tolerance" if coverage_drop <= 0.05 else "degraded"
    rows_to_write.append({
        "metric_type": "sensor_coverage", "feature_name": "full_coverage_fraction",
        "value": cur_cov["full"], "threshold": train_cov["full"] - 0.05,
        "alert_triggered": coverage_status == "degraded", "window_size": len(current_idx),
    })

    # ------------------------------------------------------------------- persist
    con, backend = db.open_ops_only()
    for r in rows_to_write:
        db.execute(
            con,
            """INSERT INTO drift_metrics
                   (model_id, metric_type, feature_name, value, threshold, alert_triggered,
                    window_size, plant_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (model_id, r["metric_type"], r["feature_name"], float(r["value"]),
             float(r["threshold"]), bool(r["alert_triggered"]), r["window_size"], pid),
        )

    overall = "healthy"
    if cal_status == "alert" or n_shifted > TOP_N_FEATURES // 2 or coverage_status == "degraded":
        overall = "degraded"
    elif n_shifted > 0:
        overall = "warning"

    return {
        "plant_id": pid, "model_id": model_id, "checked_at": now_iso, "backend": backend,
        "overall_status": overall,
        "psi_summary": {
            "features_shifted": n_shifted, "features_total": len(top_features),
            "worst_feature": worst,
        },
        "calibration": {**cal, "status": cal_status, "window_size": len(current_idx)},
        "sensor_coverage": {**cur_cov, "training_coverage": train_cov, "drift": coverage_status},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--model-id", default=None)
    args = ap.parse_args()
    report = run(args.config, args.model_id)

    print(f"=== {report['plant_id']} drift check ({report['backend']}) ===")
    print(f"  overall: {report['overall_status'].upper()}")
    psi = report["psi_summary"]
    print(f"  PSI: {psi['features_shifted']}/{psi['features_total']} features shifted"
          + (f"  worst={psi['worst_feature']['name']} ({psi['worst_feature']['psi']:.3f})"
             if psi["worst_feature"] else ""))
    cal = report["calibration"]
    print(f"  calibration: max deviation {cal['max_deviation_pp']:.1f}pp ({cal['status']})")
    cov = report["sensor_coverage"]
    print(f"  sensor coverage: full={cov['full']:.1%} "
          f"(training {cov['training_coverage']['full']:.1%})  {cov['drift']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
