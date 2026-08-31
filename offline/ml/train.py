"""Training CLI.

    python -m offline.ml.train --config configs/plant_a.yaml

Loads simulated data, builds features, trains the defect model, calibrates it, computes
both alarm thresholds, and writes everything to data/<plant_id>/model.pkl + metrics.json.

Per-plant by design (ADR-006): plant B has 42 stations, different zones and a TRUCK
variant, so its feature space is genuinely different and one ensemble cannot score both.
The pipeline is identical -- only the YAML changes.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")  # ADR-016 -- before sklearn imports

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from offline.config_schema import load_economics_config, load_plant_config  # noqa: E402
from offline.ml.defect_model import permutation_importance, train  # noqa: E402
from offline.ml.featurize import build_features  # noqa: E402
from offline.ml.thresholds import build_thresholds  # noqa: E402
from sklearn.metrics import average_precision_score  # noqa: E402

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def run(config_path: str, data_root: Path = DATA_ROOT) -> dict:
    t0 = time.time()
    cfg = load_plant_config(config_path)
    econ = load_economics_config()
    d = data_root / cfg.plant_id

    tel = pd.read_parquet(d / "telemetry.parquet")
    ck = pd.read_parquet(d / "checklists.parquet")
    ins = pd.read_parquet(d / "inspections.parquet")

    fs = build_features(cfg, tel, ck, ins)
    model = train(fs, seed=cfg.simulation.seed)

    split = fs.meta["split"].to_numpy()
    te = split == "test"
    p_te = model.predict(fs.X[te])
    y_te = fs.y[te]

    # Shifts covered by the test split, for per-shift alert rates.
    test_days = fs.meta.loc[te, "day"].nunique()
    n_shifts = test_days * econ.production["shifts_per_day"]
    units_per_shift = int(te.sum()) / max(1, n_shifts)

    escape_rate = float(ins["escaped"].sum() / max(1, ins["true_defect"].sum()))
    thr = build_thresholds(y_te, p_te, econ, units_per_shift, escape_rate, n_shifts)

    # ---------------------------------------------------------------- ORACLE CEILING
    # Score the SIMULATOR'S OWN latent propensity against the same labels. No model can
    # beat this -- it is the true data-generating probability, including the two
    # unobservable causes. Reporting our PR-AUC as a fraction of it turns an absolute
    # number nobody can calibrate into a statement about how much of the learnable
    # signal we actually captured.
    prop = ins.set_index("unit_seq")["propensity"].reindex(fs.meta["unit_seq"]).to_numpy()
    oracle = float(average_precision_score(y_te, prop[te]))

    importance = permutation_importance(model, fs, top=25, seed=cfg.simulation.seed)

    metrics = {
        "plant_id": cfg.plant_id,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": time.time() - t0,
        "escape_rate": escape_rate,
        "oracle_pr_auc": oracle,
        **model.metrics,
        "thresholds": thr.to_dict(),
        "permutation_importance": importance,
    }

    with (d / "model.pkl").open("wb") as fh:
        pickle.dump({"model": model, "feature_names": model.feature_names}, fh)
    with (d / "metrics.json").open("w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2, default=float)

    # Per-unit scores for every split, consumed by build_artifact.
    scores = pd.DataFrame(
        {
            "unit_seq": fs.meta["unit_seq"].to_numpy(),
            "unit_id": fs.meta["unit_id"].to_numpy(),
            "split": split,
            "risk_raw": model.predict_raw(fs.X),
            "calibrated_risk": model.predict(fs.X),
            "y": fs.y,
            "y_true": fs.y_true,
            "defect_mode": fs.defect_mode,
            "prediction_ts": fs.meta["prediction_ts"].to_numpy(),
            "inspection_ts": fs.meta["inspection_ts"].to_numpy(),
        }
    )
    scores["above_cost_threshold"] = (scores["calibrated_risk"] >= thr.cost_optimal).astype(int)
    scores["alert_fired"] = (scores["calibrated_risk"] >= thr.budget_constrained).astype(int)
    scores.to_parquet(d / "scores.parquet", index=False)
    fs.X.to_parquet(d / "features.parquet")
    fs.meta.to_parquet(d / "feature_meta.parquet", index=False)

    return metrics


def print_report(m: dict) -> bool:
    t = m["thresholds"]
    print(f"\n=== {m['plant_id']} ============================================")
    print(f"  trained in {m['elapsed_s']:.1f}s   {m['n_features']} features   "
          f"train/val/test = {m['n_train']}/{m['n_validate']}/{m['n_test']}")
    print(f"  test positives {m['positives_test']}  (base rate {m['base_rate_test']:.2%})")
    print()
    print(f"  PR-AUC        {m['pr_auc']:.3f}  [95% CI {m['pr_auc_ci_low']:.3f}-{m['pr_auc_ci_high']:.3f}]")
    print(f"                lift over base rate: {m['pr_auc_lift_over_base']:.1f}x")
    orc = m.get("oracle_pr_auc")
    if orc:
        print(f"  ORACLE PR-AUC {orc:.3f}  <- the simulator's own latent propensity.")
        print(f"                No model can exceed this. We capture {m['pr_auc']/orc:.0%} of it.")
    print(f"  ROC-AUC       {m['roc_auc']:.3f}  [95% CI {m['roc_auc_ci_low']:.3f}-{m['roc_auc_ci_high']:.3f}]")
    print(f"  Brier         {m['brier_test']:.4f}")
    print()
    print("  THE TWO THRESHOLDS (ADR-004) -- they disagree, and that is the finding:")
    print(f"    cost-optimal      p >= {t['cost_optimal']:.4f}"
          f"  -> {t['alerts_per_shift_at_cost_optimal']:.0f} alerts/shift, "
          f"recall {t['recall_at_cost_optimal']:.0%}")
    print(f"    budget-constrained p >= {t['budget_constrained']:.4f}"
          f"  -> {t['alerts_per_shift_at_budget']:.1f} alerts/shift, "
          f"recall {t['recall_at_budget']:.0%}, precision {t['precision_at_budget']:.0%}")
    print(f"    NOTE: at p* the model flags ~every unit. 'recall 100%' is arithmetic,")
    print(f"          not an operating policy -- nobody answers 431 alerts a shift.")
    print(f"    The decision a manager CAN make -- double the alert budget:")
    print(f"      recall {t['recall_at_budget']:.0%} -> {t['doubled_budget_recall']:.0%}"
          f"   value +${t['marginal_value_of_doubling']:,.0f}/yr"
          f"   response cost +${t['marginal_cost_of_doubling']:,.0f}/yr")
    print()
    print("  top drivers (permutation importance):")
    for row in m["permutation_importance"][:6]:
        print(f"    {row['delta_pr_auc']:+.4f}  {row['feature']}")

    ok = True
    if orc and m["pr_auc"] / orc > 0.98:
        print("\n  [WARN] within 2% of the oracle -- suspiciously easy; check for leakage.")
    if m["pr_auc"] > 0.92:
        print("\n  [WARN] PR-AUC above 0.92 -- the simulated world may be too clean.")
        ok = False
    if m["positives_test"] < 100:
        print("\n  [WARN] fewer than 100 test positives -- the CI will be very wide.")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    m = run(args.config)
    print_report(m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
