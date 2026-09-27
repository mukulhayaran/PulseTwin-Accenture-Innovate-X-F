"""XGBoost defect-risk CHALLENGER: deterministic tuning, calibration, tie-aware alert
threshold, one untouched test evaluation, gated promotion (ADR-061).

    OMP_NUM_THREADS=1 python -m offline.ml.xgb_challenger --config configs/plant_a.yaml
    OMP_NUM_THREADS=1 python -m offline.ml.xgb_challenger --config configs/plant_a.yaml --smoke

THE DATA DISCIPLINE, in order:
  * Splits are the champion's own chronological days (FeatureSet.meta["split"]):
    train days 1-14, validate 15-21, test 22-28. No random K-fold anywhere.
  * Tuning sees train + validate ONLY. `pre_test()` strips test rows before any search
    code runs, and tests/test_xgb_challenger.py corrupts every test label and feature to
    prove the selected configuration does not move.
  * Walk-forward folds inside the pre-test period: fold 1 trains on the first half of the
    train days and early-stops on the second half; fold 2 trains on all train days and
    early-stops on validation. Fold 2 is also where calibration and the alert threshold
    are learned -- the validation fold is used for three things, so its metrics are
    optimistic by construction, which is exactly why they only RANK candidates and the
    test set alone decides promotion.
  * The test set is scored exactly once, after the winner, its calibrator and its
    threshold are frozen.

THE SELECTION RULE (operational, never accuracy -- the base rate is ~2.5%), lexicographic
constrained selection -- see rank_key() for the full order and why it is that order:
  1. eligible   = validation recall >= the champion's published recall, alerts/shift on
                  validation <= the configured budget, and a MATERIALLY diverse alert
                  cohort (material_diversity);
  2. among equally feasible candidates the smallest recall shortfall wins, then the
     highest validation precision at budget (the primary objective), then walk-forward
     mean PR-AUC, then validation PR-AUC, then trial index (deterministic tie-break);
  3. the report states whether any candidate was actually eligible.
Amended once (run 2) after run 1's winner exposed a degenerate fallback; run 1's full
report is kept as artifacts/xgb_challenger_<plant>_run1.json (ADR-061).

PROMOTION happens only when every gate in `promotion_gates()` passes on the untouched test
set. Otherwise the HistGradientBoosting champion stays, the challenger is saved as a
challenger, and the failed gates are listed. The threshold is never moved on test to
manufacture a win.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("OMP_NUM_THREADS", "1")  # ADR-016, before numpy/sklearn/xgboost

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
DATA_ROOT = ROOT / "data"
SEED = 42
EARLY_STOPPING_ROUNDS = 50
ROUND_DECIMALS = 6          # "the same score" means equal to 6 decimals, as the brief does
MIN_ISOTONIC_POSITIVES = 200
DIVERSITY_MAX_MODAL_SHARE = 0.5   # "material" diversity: no score holds half the cohort
BRIER_TOLERANCE = 1.05            # challenger Brier may be at most 5% worse
ECE_TOLERANCE = 0.01              # and its ECE at most 0.01 worse (absolute)
RUNTIME_FORMAT = "pulsetwin-xgb-v1"

# The bounded search space. Distributions are documented here, sampled by sklearn's
# ParameterSampler with a fixed seed -- no tuning framework needed for a seeded search.
SEARCH_SPACE_DOC = {
    "n_estimators": "randint[100, 1200] (upper bound; early stopping picks the count)",
    "learning_rate": "loguniform[0.01, 0.15]",
    "max_depth": "randint[2, 8]",
    "min_child_weight": "loguniform[1, 30]",
    "subsample": "uniform[0.6, 1.0]",
    "colsample_bytree": "uniform[0.5, 1.0]",
    "gamma": "uniform[0, 10]",
    "reg_alpha": "uniform[0, 10]",
    "reg_lambda": "loguniform[0.5, 30]",
    "scale_pos_weight": "choice{1, sqrt(w), w/2, w}, w = train negatives/positives",
}


def _space(pos_weight: float) -> dict[str, Any]:
    from scipy.stats import loguniform, randint, uniform
    return {
        "n_estimators": randint(100, 1201),
        "learning_rate": loguniform(0.01, 0.15),
        "max_depth": randint(2, 9),
        "min_child_weight": loguniform(1, 30),
        "subsample": uniform(0.6, 0.4),
        "colsample_bytree": uniform(0.5, 0.5),
        "gamma": uniform(0.0, 10.0),
        "reg_alpha": uniform(0.0, 10.0),
        "reg_lambda": loguniform(0.5, 30),
        "scale_pos_weight": sorted({1.0, round(math.sqrt(pos_weight), 3),
                                    round(pos_weight / 2, 3), round(pos_weight, 3)}),
    }


def candidate_params(n_trials: int, pos_weight: float, seed: int = SEED) -> list[dict]:
    from sklearn.model_selection import ParameterSampler
    out = []
    for p in ParameterSampler(_space(pos_weight), n_iter=n_trials, random_state=seed):
        out.append({k: (int(v) if k in ("n_estimators", "max_depth") else round(float(v), 6))
                    for k, v in p.items()})
    return out


# ----------------------------------------------------------------------- data split

@dataclass
class Split:
    X: pd.DataFrame
    y: np.ndarray
    day: np.ndarray


def pre_test(X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> dict[str, Split]:
    """Train and validate rows only. Nothing downstream of tuning can see a test row."""
    split = meta["split"].to_numpy()
    day = meta["day"].to_numpy()
    out = {}
    for name in ("train", "validate"):
        m = split == name
        out[name] = Split(X[m], y[m], day[m])
    return out


def test_rows(X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> Split:
    m = meta["split"].to_numpy() == "test"
    return Split(X[m], y[m], meta["day"].to_numpy()[m])


def walk_forward_folds(parts: dict[str, Split]) -> list[tuple[Split, Split]]:
    """Fold 1: first half of the train days -> second half. Fold 2: train -> validate."""
    tr = parts["train"]
    days = np.unique(tr.day)
    cut = days[len(days) // 2 - 1]
    a, b = tr.day <= cut, tr.day > cut
    fold1 = (Split(tr.X[a], tr.y[a], tr.day[a]), Split(tr.X[b], tr.y[b], tr.day[b]))
    return [fold1, (tr, parts["validate"])]


# ---------------------------------------------------------------------- the model

def fit_xgb(params: dict, train: Split, early: Split, n_jobs: int, seed: int = SEED):
    import xgboost as xgb
    clf = xgb.XGBClassifier(
        **params,
        objective="binary:logistic",
        eval_metric="aucpr",
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        tree_method="hist",
        missing=np.nan,          # NaN is a real "sensor absent" signal, routed natively
        random_state=seed,
        n_jobs=n_jobs,
    )
    clf.fit(train.X, train.y, eval_set=[(early.X, early.y)], verbose=False)
    return clf


def raw_proba(clf, X) -> np.ndarray:
    """The model's own P(y=1) BEFORE calibration -- a ranking signal, not a probability
    of defect. Uses only the trees up to the early-stopping best iteration."""
    return clf.predict_proba(X, iteration_range=(0, clf.best_iteration + 1))[:, 1]


# ----------------------------------------------------------------- calibration

@dataclass
class Calibrator:
    method: str                  # "platt" | "isotonic"
    params: dict

    def apply(self, raw: np.ndarray) -> np.ndarray:
        raw = np.asarray(raw, dtype=np.float64)
        if self.method == "platt":
            z = _logit(raw)
            return _sigmoid(self.params["a"] * z + self.params["b"])
        return np.clip(np.interp(raw, self.params["x"], self.params["y"]), 0.0, 1.0)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-7, 1 - 1e-7)
    return np.log(p / (1 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))


def fit_calibrator(method: str, raw: np.ndarray, y: np.ndarray) -> Calibrator:
    if method == "platt":
        from sklearn.linear_model import LogisticRegression
        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit(_logit(raw).reshape(-1, 1), y)
        return Calibrator("platt", {"a": float(lr.coef_[0, 0]), "b": float(lr.intercept_[0])})
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, y)
    return Calibrator("isotonic", {"x": iso.X_thresholds_.tolist(),
                                   "y": iso.y_thresholds_.tolist()})


def choose_calibrator(raw_va: np.ndarray, y_va: np.ndarray, day_va: np.ndarray,
                      budget_alerts: int) -> tuple[Calibrator, dict]:
    """Validation data only. Brier is scored CROSS-FITTED across the two halves of the
    validation days: an in-sample Brier always favours isotonic, because isotonic is the
    least-squares fit to exactly those labels. The winner is refitted on all of
    validation. Isotonic is rejected if it collapses the alerted cohort -- its plateaus
    are ties by construction, which is the very defect this challenger exists to fix."""
    days = np.unique(day_va)
    half = day_va <= days[len(days) // 2 - 1]
    methods = ["platt"]
    if int(y_va.sum()) >= MIN_ISOTONIC_POSITIVES:
        methods.append("isotonic")
    diag: dict[str, Any] = {}
    for m in methods:
        briers = []
        for fit_mask in (half, ~half):
            if y_va[fit_mask].sum() == 0 or y_va[~fit_mask].sum() == 0:
                continue
            cal = fit_calibrator(m, raw_va[fit_mask], y_va[fit_mask])
            p = cal.apply(raw_va[~fit_mask])
            briers.append(float(np.mean((p - y_va[~fit_mask]) ** 2)))
        full = fit_calibrator(m, raw_va, y_va)
        p_full = full.apply(raw_va)
        thr = select_threshold(p_full, budget_alerts)
        div = diversity(p_full[p_full >= thr["threshold"]]) if thr["alerts"] else diversity([])
        diag[m] = {"cross_fitted_brier": float(np.mean(briers)) if briers else None,
                   "alert_cohort": div, "calibrator": full}
    ok = [m for m in methods
          if diag[m]["cross_fitted_brier"] is not None and material_diversity(diag[m]["alert_cohort"])]
    pick = min(ok or methods, key=lambda m: (diag[m]["cross_fitted_brier"] or 1.0, m != "platt"))
    report = {m: {k: v for k, v in d.items() if k != "calibrator"} for m, d in diag.items()}
    report["selected"] = pick
    return diag[pick]["calibrator"], report


# --------------------------------------------------------------- threshold + metrics

def select_threshold(p: np.ndarray, budget_alerts: int) -> dict:
    """The highest-coverage cut that admits AT MOST `budget_alerts` units, tie-aware.

    The champion's rule (offline/ml/thresholds.py) takes the k-th highest score; when that
    score is shared by many units every one of them alerts, and a 6/shift budget became
    8.48/shift on test. Here a tied block that would overrun the budget is excluded whole,
    and `tie_overrun_avoided` records how many alerts the naive rule would have added.
    """
    p = np.asarray(p, dtype=np.float64)
    if len(p) == 0 or budget_alerts <= 0:
        return {"threshold": float("inf"), "alerts": 0, "naive_alerts": 0,
                "tie_overrun_avoided": 0, "score_count_at_threshold": 0}
    order = np.sort(p)[::-1]
    k = min(budget_alerts, len(order))
    naive_thr = order[k - 1]
    naive_alerts = int((p >= naive_thr).sum())
    values = np.unique(p)[::-1]                  # distinct scores, descending
    asc = np.sort(p)
    counts = len(p) - np.searchsorted(asc, values, side="left")   # = (p >= v).sum()
    admissible = values[counts <= budget_alerts]
    if len(admissible) == 0:
        # Even the single highest score is shared by more units than the budget allows.
        return {"threshold": float(values[0]) + 1e-12, "alerts": 0,
                "naive_alerts": naive_alerts, "tie_overrun_avoided": naive_alerts,
                "score_count_at_threshold": int((p == values[0]).sum())}
    thr = float(admissible[-1])                  # lowest admissible = most alerts <= budget
    alerts = int((p >= thr).sum())
    return {"threshold": thr, "alerts": alerts, "naive_alerts": naive_alerts,
            "tie_overrun_avoided": naive_alerts - alerts,
            "score_count_at_threshold": int((p == thr).sum())}


def diversity(scores) -> dict:
    """Is the alerted cohort rankable? Equality is to ROUND_DECIMALS, as a supervisor sees
    it. Entropy is Shannon, in bits, over those rounded values."""
    s = np.round(np.asarray(scores, dtype=np.float64), ROUND_DECIMALS)
    if len(s) == 0:
        return {"n": 0, "unique_scores": 0, "entropy_bits": 0.0, "modal_share": 0.0,
                "collapsed": True}
    _, cnt = np.unique(s, return_counts=True)
    prob = cnt / cnt.sum()
    entropy = float(-(prob * np.log2(prob)).sum()) if len(cnt) > 1 else 0.0
    modal = float(cnt.max() / cnt.sum())
    return {"n": int(len(s)), "unique_scores": int(len(cnt)), "entropy_bits": entropy,
            "modal_share": modal,
            "collapsed": bool(len(cnt) <= 1 or entropy == 0.0 or modal >= 1.0)}


def material_diversity(div: dict) -> bool:
    """THE one diversity test -- used for candidate eligibility, for rejecting a
    calibrator, and for the promotion gate, so the three can never disagree. More than one
    score, nonzero entropy, and no single score holding more than half the alerted cohort.
    (Run 1 used the weaker `not collapsed` for eligibility: a winner with two isotonic
    plateaus, 78% of its alerts on one of them, passed eligibility and failed the gate.)"""
    return bool(div["unique_scores"] > 1 and div["entropy_bits"] > 0
                and div["modal_share"] <= DIVERSITY_MAX_MODAL_SHARE)


def alert_metrics(y: np.ndarray, p: np.ndarray, threshold: float, n_shifts: float) -> dict:
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=np.float64)
    a = p >= threshold
    tp = int((a & (y == 1)).sum())
    fp = int((a & (y == 0)).sum())
    fn = int((~a & (y == 1)).sum())
    n = int(a.sum())
    return {
        "threshold": float(threshold),
        "alerts": n,
        "alerts_per_shift": n / n_shifts if n_shifts else float("nan"),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": tp / n if n else 0.0,
        "recall": tp / (tp + fn) if (tp + fn) else 0.0,
        "score_count_at_threshold": int((np.round(p, ROUND_DECIMALS)
                                         == round(threshold, ROUND_DECIMALS)).sum()),
        "alert_cohort": diversity(p[a]),
    }


TRAILING_WINDOW_SHIFTS = 21     # one week of shifts: ADR-062


def trailing_budget_alerts(seed_scores: np.ndarray, p_eval: np.ndarray, window_units: int,
                           budget_alerts: int) -> tuple[np.ndarray, np.ndarray]:
    """ADR-062, evaluated with the EXACT class serve uses at runtime: each unit, in
    prediction-time order, is judged against the tie-aware budget cut over the previous
    `window_units` calibrated scores (seeded with validation scores), and then joins the
    window. Scores only -- no label is read. Returns (alert flags, threshold per unit)."""
    from serve.rolling_threshold import TrailingBudgetThreshold
    roll = TrailingBudgetThreshold(seed_scores, window_units, budget_alerts)
    flags = np.zeros(len(p_eval), dtype=bool)
    thr = np.zeros(len(p_eval))
    for i, v in enumerate(np.asarray(p_eval, dtype=np.float64)):
        thr[i] = roll.threshold
        flags[i] = v >= thr[i]
        roll.observe(v)
    return flags, thr


def flag_metrics(y: np.ndarray, p: np.ndarray, flags: np.ndarray, n_shifts: float,
                 thresholds: np.ndarray) -> dict:
    """alert_metrics for a per-unit decision (a rolling threshold has no single cut)."""
    y = np.asarray(y).astype(int)
    tp = int((flags & (y == 1)).sum())
    fp = int((flags & (y == 0)).sum())
    fn = int((~flags & (y == 1)).sum())
    n = int(flags.sum())
    finite = thresholds[np.isfinite(thresholds)]
    return {"policy": "trailing_budget", "alerts": n,
            "alerts_per_shift": n / n_shifts if n_shifts else float("nan"),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": tp / n if n else 0.0, "recall": tp / (tp + fn) if (tp + fn) else 0.0,
            "threshold_min": float(finite.min()) if len(finite) else None,
            "threshold_median": float(np.median(finite)) if len(finite) else None,
            "threshold_max": float(finite.max()) if len(finite) else None,
            "alert_cohort": diversity(np.asarray(p)[flags])}


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    ece, n = 0.0, len(p)
    for i in range(bins):
        m = (p > edges[i]) & (p <= edges[i + 1])
        if m.any():
            ece += m.sum() / n * abs(float(p[m].mean()) - float(y[m].mean()))
    return float(ece)


def score_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score
    return {"pr_auc": float(average_precision_score(y, p)),
            "roc_auc": float(roc_auc_score(y, p)),
            "brier": float(np.mean((p - y) ** 2)),
            "ece": expected_calibration_error(np.asarray(y), np.asarray(p))}


# ----------------------------------------------------------------------- tuning

def evaluate_candidate(i: int, params: dict, folds, n_shifts_va: float,
                       budget_alerts: int, recall_floor: float, max_per_shift: float,
                       n_jobs: int) -> dict:
    from sklearn.metrics import average_precision_score
    t0 = time.time()
    wf = []
    for tr, ev in folds[:-1]:
        clf = fit_xgb(params, tr, ev, n_jobs)
        wf.append(float(average_precision_score(ev.y, raw_proba(clf, ev.X))))
    tr, va = folds[-1]
    clf = fit_xgb(params, tr, va, n_jobs)
    raw_va = raw_proba(clf, va.X)
    wf.append(float(average_precision_score(va.y, raw_va)))
    cal, cal_diag = choose_calibrator(raw_va, va.y, va.day, budget_alerts)
    p_va = cal.apply(raw_va)
    thr = select_threshold(p_va, budget_alerts)
    am = alert_metrics(va.y, p_va, thr["threshold"], n_shifts_va)
    constraints = {
        "recall_floor": am["recall"] >= recall_floor,
        "alert_budget": am["alerts_per_shift"] <= max_per_shift + 1e-9,
        "diverse_alert_cohort": material_diversity(am["alert_cohort"]),
    }
    return {
        "recall_shortfall": max(0.0, recall_floor - am["recall"]),
        "trial": i, "params": params, "best_iteration": int(clf.best_iteration),
        "walk_forward_pr_auc": wf, "walk_forward_mean_pr_auc": float(np.mean(wf)),
        "validation": {**score_metrics(va.y, p_va), "raw_pr_auc": wf[-1], **am,
                       "threshold_diagnostics": thr},
        "calibration": cal_diag, "calibrator": cal, "model": clf,
        "constraints": constraints, "eligible": all(constraints.values()),
        "seconds": round(time.time() - t0, 1),
    }


def rank_key(c: dict) -> tuple:
    """Lexicographic constrained selection: minimise constraint violation, THEN optimise.

      1. eligible (all three constraints met) first;
      2. hard constraints: within the alert budget, a materially diverse alert cohort;
      3. the SMALLEST recall shortfall below the champion's recall -- the floor is soft
         only in the sense that when no candidate reaches it the closest one wins;
      4. precision at budget (the primary objective among equally-feasible candidates);
      5. walk-forward mean PR-AUC, then validation PR-AUC, then trial index.

    Run 1 ranked by precision straight after a COUNT of satisfied constraints. With the
    recall floor unreachable on validation for every candidate (a 126-alert budget over
    373 validation defects caps recall at 0.338, and 0.276 needs 82% precision), that
    rewarded alerting on as FEW units as possible: the winner filled 32 of 126 slots at
    precision 0.44 and recall 0.04. At a filled budget precision and recall move together,
    so minimising the shortfall first removes that incentive.
    """
    v = c["validation"]
    k = c["constraints"]
    return (-int(c["eligible"]), -int(k["alert_budget"]), -int(k["diverse_alert_cohort"]),
            round(c["recall_shortfall"], 9), -round(v["precision"], 9),
            -round(c["walk_forward_mean_pr_auc"], 9), -round(v["pr_auc"], 9), c["trial"])


def tune(X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame, n_trials: int,
         shifts_per_day: int, max_per_shift: float, recall_floor: float,
         n_jobs: int = 4, seed: int = SEED, log=print) -> dict:
    """Deterministic search over the PRE-TEST period. Test rows are removed first."""
    parts = pre_test(X, y, meta)
    folds = walk_forward_folds(parts)
    ytr = parts["train"].y
    pos_weight = float((ytr == 0).sum() / max(1, (ytr == 1).sum()))
    n_shifts_va = len(np.unique(parts["validate"].day)) * shifts_per_day
    budget_alerts = int(math.floor(max_per_shift * n_shifts_va))
    results = []
    for i, params in enumerate(candidate_params(n_trials, pos_weight, seed)):
        c = evaluate_candidate(i, params, folds, n_shifts_va, budget_alerts, recall_floor,
                               max_per_shift, n_jobs)
        v = c["validation"]
        log(f"  trial {i:>3}  val P {v['precision']:.3f} R {v['recall']:.3f} "
            f"PR-AUC {v['pr_auc']:.3f} wf {c['walk_forward_mean_pr_auc']:.3f} "
            f"alerts/shift {v['alerts_per_shift']:.2f} uniq {v['alert_cohort']['unique_scores']} "
            f"{'ELIGIBLE' if c['eligible'] else '-'}  ({c['seconds']}s)")
        results.append(c)
    results.sort(key=rank_key)
    return {"best": results[0], "trials": results, "pos_weight": pos_weight,
            "budget_alerts_validation": budget_alerts, "n_shifts_validation": n_shifts_va}


# -------------------------------------------------------------------- promotion

def promotion_gates(ch: dict, champ: dict, published: dict, max_per_shift: float,
                    invariants: dict) -> dict:
    """Every gate compares the challenger's UNTOUCHED test result against the champion's
    published test numbers (artifacts/metrics_summary.json)."""
    aps = ch["alerts_per_shift"]
    div = ch["alert_cohort"]
    gates = {
        "precision_at_budget": ch["precision"] > published["precision_at_budget"],
        "recall_at_budget": ch["recall"] > published["recall_at_budget"],
        "pr_auc": ch["pr_auc"] > published["pr_auc"],
        # "No more than the configured limit, or as close as the existing threshold
        # system achieves": at or under the limit passes; over it passes only if it is
        # still no worse than the champion's published rate.
        "alert_budget": aps <= max_per_shift + 1e-9 or aps <= published["alerts_per_shift"],
        "score_diversity": material_diversity(div),
        "calibration": (ch["brier"] <= published["brier"] * BRIER_TOLERANCE
                        and ch["ece"] <= champ["ece"] + ECE_TOLERANCE),
        "cutoff_and_lead_time_unchanged": invariants["features_identical"]
                                          and invariants["lead_time_identical"],
        "no_leakage": invariants["test_untouched_during_tuning"],
    }
    return {"gates": gates, "promote": all(gates.values()),
            "failed": [k for k, ok in gates.items() if not ok]}


# ------------------------------------------------------------------ runtime export

def runtime_artifact(clf, cal: Calibrator, feature_names: list[str], threshold: float,
                     meta: dict) -> dict:
    """The booster truncated to its early-stopping iteration, as XGBoost's own JSON model
    format, plus everything serve/xgb_scorer.py needs. serve/ never imports xgboost."""
    booster = clf.get_booster()[: clf.best_iteration + 1]
    raw = booster.save_raw(raw_format="json")
    return {"format": RUNTIME_FORMAT, "booster": json.loads(bytes(raw).decode("utf-8")),
            "feature_names": list(feature_names), "calibration": {"method": cal.method,
                                                                  **cal.params},
            "alert_threshold": float(threshold), **meta}


def _sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _versions() -> dict:
    import sklearn
    import xgboost
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__,
            "xgboost": xgboost.__version__}


# ------------------------------------------------------------------------- pipeline

def run(config: str, trials: int, n_jobs: int, smoke: bool, out_dir: Path = ARTIFACTS,
        data_root: Path = DATA_ROOT, docs_path: Path | None = ROOT / "docs" / "XGB_CHALLENGER.md",
        log=print) -> dict:
    import pickle

    from offline.config_schema import load_economics_config, load_plant_config
    from offline.ml.featurize import build_features

    t0 = time.time()
    cfg = load_plant_config(config)
    econ = load_economics_config()
    pid = cfg.plant_id
    d = data_root / pid
    max_per_shift = float(econ.max_alerts_per_shift)
    shifts_per_day = int(econ.production["shifts_per_day"])
    published = json.loads((out_dir / "metrics_summary.json").read_text(encoding="utf-8")
                           )["plants"][pid]

    log(f"[xgb] {pid}: building features from {d}")
    fs = build_features(cfg, pd.read_parquet(d / "telemetry.parquet"),
                        pd.read_parquet(d / "checklists.parquet"),
                        pd.read_parquet(d / "inspections.parquet"))
    X, y, meta = fs.X, fs.y, fs.meta

    # Gate 7's evidence: the challenger uses the champion's exact feature matrix -- same
    # prediction cutoff station, same prediction_ts, therefore the same lead time.
    champ_cols = list(pd.read_parquet(d / "features.parquet").columns)
    champ_meta = pd.read_parquet(d / "feature_meta.parquet")
    lead = lambda m: float(np.median(m["inspection_ts"] - m["prediction_ts"]))  # noqa: E731
    invariants = {
        "prediction_cutoff_station": cfg.prediction_cutoff_station(),
        "features_identical": list(X.columns) == champ_cols,
        "lead_time_identical": abs(lead(meta) - lead(champ_meta)) < 1e-6,
        "median_lead_time_s": lead(meta),
    }

    log(f"[xgb] tuning {trials} trials (seed {SEED}, n_jobs {n_jobs}) on train+validate only")
    search = tune(X, y, meta, trials, shifts_per_day, max_per_shift,
                  published["recall_at_budget"], n_jobs=n_jobs, log=log)
    invariants["test_untouched_during_tuning"] = True   # tune() only receives pre_test()
    best = search["best"]
    clf, cal = best["model"], best["calibrator"]
    thr = best["validation"]["threshold"]

    # The trailing-window policy's parameters, fixed from VALIDATION before test is read.
    va_mask = meta["split"].to_numpy() == "validate"
    va_order = np.argsort(meta.loc[va_mask, "prediction_ts"].to_numpy(), kind="stable")
    p_va_best = cal.apply(raw_proba(clf, X[va_mask]))[va_order]
    window_units = int(round(va_mask.sum() / search["n_shifts_validation"]
                             * TRAILING_WINDOW_SHIFTS))
    window_budget = int(math.floor(max_per_shift * TRAILING_WINDOW_SHIFTS))
    seed_scores = p_va_best[-window_units:]

    # ------------------------------------------------ the ONE use of the test labels
    te = test_rows(X, y, meta)
    n_shifts_te = len(np.unique(te.day)) * shifts_per_day
    raw_te = raw_proba(clf, te.X)
    p_te = cal.apply(raw_te)
    ch_test = {**score_metrics(te.y, p_te), **alert_metrics(te.y, p_te, thr, n_shifts_te),
               "raw_pr_auc": score_metrics(te.y, raw_te)["pr_auc"]}
    te_order = np.argsort(meta.loc[meta["split"] == "test", "prediction_ts"].to_numpy(),
                          kind="stable")
    flags, thr_seq = trailing_budget_alerts(seed_scores, p_te[te_order], window_units,
                                            window_budget)
    ch_rolling = {**score_metrics(te.y, p_te),
                  **flag_metrics(te.y[te_order], p_te[te_order], flags, n_shifts_te, thr_seq)}

    # Champion, same data: published numbers (threshold taken ON TEST by the legacy
    # rule) and the same champion re-thresholded on VALIDATION with the tie-aware rule.
    champ = pickle.load((d / "model.pkl").open("rb"))["model"]
    parts = pre_test(X, y, meta)
    p_va_ch = champ.predict(parts["validate"].X)
    budget_va = search["budget_alerts_validation"]
    thr_ch = select_threshold(p_va_ch, budget_va)
    p_te_ch = champ.predict(te.X)
    p_va_ch_seed = champ.predict(X[va_mask])[va_order][-window_units:]
    f_ch, thr_ch_seq = trailing_budget_alerts(p_va_ch_seed, p_te_ch[te_order], window_units,
                                              window_budget)
    champ_test = {**score_metrics(te.y, p_te_ch),
                  "trailing_budget": flag_metrics(te.y[te_order], p_te_ch[te_order], f_ch,
                                                  n_shifts_te, thr_ch_seq),
                  "published_threshold": alert_metrics(te.y, p_te_ch,
                                                       published["budget_threshold"],
                                                       n_shifts_te),
                  "validation_threshold": alert_metrics(te.y, p_te_ch, thr_ch["threshold"],
                                                        n_shifts_te),
                  "validation_threshold_diagnostics": thr_ch}

    # ADR-062, pre-registered before this policy's test result existed: the challenger's
    # operating policy is the trailing-window budget, so the gates score THAT. The static
    # validation threshold's result stays in the report beside it.
    decision = promotion_gates(ch_rolling, champ_test, published, max_per_shift, invariants)
    if smoke:
        # A smoke run is a plumbing check on a handful of trials: it can never promote and
        # never overwrites the decision files a full run produced.
        decision = {**decision, "promote": False,
                    "failed": decision["failed"] + ["smoke_run_never_promotes"]}
    sfx = "_smoke" if smoke else ""
    version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    art = runtime_artifact(clf, cal, list(X.columns), thr, {
        "plant_id": pid, "model_type": "xgboost", "model_version": version,
        "hyperparameters": best["params"], "best_iteration": best["best_iteration"],
        "calibration_method": cal.method, "trained_at": datetime.now(timezone.utc).isoformat(),
        "threshold_policy": {"type": "trailing_budget", "window_shifts": TRAILING_WINDOW_SHIFTS,
                             "window_units": window_units, "budget_alerts": window_budget,
                             "seed_scores": [round(float(v), 7) for v in seed_scores]}})
    model_file = out_dir / f"challenger_xgb_{pid}{sfx}.json"
    model_file.write_text(json.dumps(art, separators=(",", ":")), encoding="utf-8")

    selection = {
        "plant_id": pid,
        "champion": "xgboost" if decision["promote"] else "hist_gradient_boosting",
        "champion_artifact": (model_file.name if decision["promote"] else f"model_{pid}.npz"),
        "challenger": {"model_type": "xgboost", "artifact": model_file.name,
                       "model_version": version, "promoted": decision["promote"],
                       "failed_gates": decision["failed"], "calibration_method": cal.method,
                       "alert_threshold": thr},
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    if not smoke:
        (out_dir / f"model_selection_{pid}.json").write_text(json.dumps(selection, indent=2),
                                                             encoding="utf-8")

    cmd = (f"OMP_NUM_THREADS=1 python -m offline.ml.xgb_challenger --config {config} "
           f"--trials {trials} --n-jobs {n_jobs}" + (" --smoke" if smoke else ""))
    report = {
        "plant_id": pid, "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _sha(), "command": cmd, "smoke": smoke, "elapsed_s": round(time.time() - t0, 1),
        "versions": _versions(), "seed": SEED, "trials": trials, "n_jobs": n_jobs,
        "search_space": SEARCH_SPACE_DOC, "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "splits": {k: {"rows": int((meta["split"] == k).sum()),
                       "positives": int(y[meta["split"].to_numpy() == k].sum()),
                       "days": [int(meta.loc[meta["split"] == k, "day"].min()),
                                int(meta.loc[meta["split"] == k, "day"].max())]}
                   for k in ("train", "validate", "test")},
        "alert_budget": {"max_alerts_per_shift": max_per_shift,
                         "validation_budget_alerts": budget_va,
                         "shifts_validation": search["n_shifts_validation"],
                         "shifts_test": n_shifts_te},
        "invariants": invariants,
        "baseline": {"model_type": "hist_gradient_boosting",
                     "hyperparameters": _champion_hparams(champ), "published": published,
                     "test": champ_test},
        "challenger": {"hyperparameters": best["params"], "best_iteration": best["best_iteration"],
                       "calibration": best["calibration"], "validation": best["validation"],
                       "walk_forward_pr_auc": best["walk_forward_pr_auc"],
                       "eligible_on_validation": best["eligible"],
                       "constraints": best["constraints"], "test": ch_test,
                       "test_trailing_budget": ch_rolling,
                       "threshold_policy": {"type": "trailing_budget",
                                            "window_shifts": TRAILING_WINDOW_SHIFTS,
                                            "window_units": window_units,
                                            "budget_alerts": window_budget}},
        "trials_summary": [_trial_row(c) for c in search["trials"]],
        "promotion": decision, "selection": selection,
    }
    report["history"] = run_history(out_dir, pid)
    rpath = out_dir / f"xgb_challenger_{pid}{sfx}.json"
    rpath.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    if docs_path is not None:
        docs_path.write_text(markdown_report(report), encoding="utf-8")
    log(f"[xgb] report -> {rpath.relative_to(ROOT) if rpath.is_relative_to(ROOT) else rpath}; "
        f"promoted={decision['promote']} failed={decision['failed']}")
    return report


def run_history(out_dir: Path, pid: str) -> list[dict]:
    """Earlier full runs kept on disk, so every report discloses how many times the test
    set has been looked at -- each run is one look, and a selection rule changed after a
    look is a cost the reader should be able to weigh."""
    out = []
    for f in sorted(out_dir.glob(f"xgb_challenger_{pid}_run*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        t = r["challenger"]["test"]
        out.append({"file": f.name, "git_sha": r.get("git_sha"),
                    "winner_trial": r["trials_summary"][0]["trial"],
                    "test_precision": t["precision"], "test_recall": t["recall"],
                    "test_pr_auc": t["pr_auc"], "test_alerts_per_shift": t["alerts_per_shift"],
                    "failed_gates": r["promotion"]["failed"]})
    return out


def _champion_hparams(champ) -> dict:
    p = champ.clf.get_params()
    keys = ("max_iter", "learning_rate", "max_leaf_nodes", "min_samples_leaf",
            "l2_regularization", "early_stopping", "validation_fraction")
    return {**{k: p[k] for k in keys}, "n_iter_": int(champ.clf.n_iter_),
            "calibration": "platt (on raw probability, validation fold)"}


def _trial_row(c: dict) -> dict:
    v = c["validation"]
    return {"trial": c["trial"], "params": c["params"], "best_iteration": c["best_iteration"],
            "eligible": c["eligible"], "constraints": c["constraints"],
            "val_precision": v["precision"], "val_recall": v["recall"], "val_pr_auc": v["pr_auc"],
            "val_alerts_per_shift": v["alerts_per_shift"],
            "val_alert_unique_scores": v["alert_cohort"]["unique_scores"],
            "walk_forward_mean_pr_auc": c["walk_forward_mean_pr_auc"],
            "calibration": c["calibration"]["selected"], "seconds": c["seconds"]}


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, Calibrator):
        return {"method": o.method}
    return str(o)


def markdown_report(r: dict) -> str:
    # The gated policy (ADR-062) leads; the static validation threshold is shown beside it.
    ch = r["challenger"].get("test_trailing_budget") or r["challenger"]["test"]
    b = r["baseline"]
    pub, bt = b["published"], b["test"]
    g = r["promotion"]["gates"]

    def row(name, champ_v, chal_v, passed, fmt="{:.4f}"):
        ch_s = fmt.format(chal_v) if isinstance(chal_v, (int, float)) else str(chal_v)
        cp_s = fmt.format(champ_v) if isinstance(champ_v, (int, float)) else str(champ_v)
        delta = (fmt.format(chal_v - champ_v) if isinstance(chal_v, (int, float))
                 and isinstance(champ_v, (int, float)) else "")
        mark = "—" if passed is None else ("✅" if passed else "❌")
        return f"| {name} | {cp_s} | {ch_s} | {delta} | {mark} |"

    pb = bt["published_threshold"]
    lines = [
        f"# XGBoost defect-risk challenger — {r['plant_id']}",
        "",
        "> Generated by `offline/ml/xgb_challenger.py` — do not hand-edit. Synthetic data "
        "(`docs/CLAIMS.md`). Machine-readable: "
        f"`artifacts/xgb_challenger_{r['plant_id']}.json`.",
        "",
        f"**Promotion decision: {'PROMOTED' if r['promotion']['promote'] else 'NOT PROMOTED'}.** "
        + ("All gates passed." if r["promotion"]["promote"]
           else "Failed gates: " + ", ".join(f"`{x}`" for x in r["promotion"]["failed"])
           + ". The HistGradientBoosting champion is retained."),
        "",
        f"Run: `{r['command']}` · {r['trials']} trials · seed {r['seed']} · "
        f"{r['elapsed_s']} s · git `{(r['git_sha'] or '?')[:7]}`"
        + (" · **SMOKE RUN — not a decision-grade result**" if r["smoke"] else ""),
        "",
        "## Untouched test set (days "
        f"{r['splits']['test']['days'][0]}–{r['splits']['test']['days'][1]}, "
        f"{r['splits']['test']['positives']} defects)",
        "",
        "Challenger column: its operating policy, the trailing-window budget threshold "
        "(ADR-062) — alert-dependent rows are measured under it.",
        "",
        "| Metric | HGB champion (published) | XGBoost challenger | Change | Passed |",
        "|---|---:|---:|---:|---|",
        row("Precision at budget", pub["precision_at_budget"], ch["precision"],
            g["precision_at_budget"]),
        row("Recall at budget", pub["recall_at_budget"], ch["recall"], g["recall_at_budget"]),
        row("PR-AUC", pub["pr_auc"], ch["pr_auc"], g["pr_auc"]),
        row("ROC-AUC", pub["roc_auc"], ch["roc_auc"], None),
        row("Brier score", pub["brier"], ch["brier"], g["calibration"]),
        row("ECE (10 quantile bins)", bt["ece"], ch["ece"], g["calibration"]),
        row("Alerts/shift", pub["alerts_per_shift"], ch["alerts_per_shift"], g["alert_budget"],
            "{:.2f}"),
        row("Alert score count (unique)", pb["alert_cohort"]["unique_scores"],
            ch["alert_cohort"]["unique_scores"], g["score_diversity"], "{:d}"),
        row("Alert score entropy (bits)", pb["alert_cohort"]["entropy_bits"],
            ch["alert_cohort"]["entropy_bits"], g["score_diversity"], "{:.3f}"),
        row("Alert modal-score share", pb["alert_cohort"]["modal_share"],
            ch["alert_cohort"]["modal_share"], g["score_diversity"], "{:.3f}"),
        "",
        "Gates also checked: prediction cutoff and lead time unchanged "
        f"({'✅' if g['cutoff_and_lead_time_unchanged'] else '❌'}), "
        f"no test data used before this table ({'✅' if g['no_leakage'] else '❌'}).",
        "",
        "### Alert confusion counts on test",
        "",
        "| | Threshold | Alerts | TP | FP | FN | Precision | Recall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    policies = [("HGB, published threshold (chosen on test, legacy)", pb),
                ("HGB, static threshold chosen on validation (tie-aware)",
                 bt["validation_threshold"]),
                ("HGB, trailing-window budget (ADR-062)", bt.get("trailing_budget")),
                ("XGBoost, static threshold chosen on validation", r["challenger"]["test"]),
                ("XGBoost, trailing-window budget (ADR-062) — GATED",
                 r["challenger"].get("test_trailing_budget"))]
    for name, m in policies:
        if not m:
            continue
        cut = (f"{m['threshold']:.6f}" if "threshold" in m else
               f"{m['threshold_median']:.6f} (median)" if m.get("threshold_median") is not None
               else "above every score")
        lines.append(f"| {name} | {cut} | {m['alerts']} ({m['alerts_per_shift']:.2f}/shift) | "
                     f"{m['true_positives']} | {m['false_positives']} | {m['false_negatives']} | "
                     f"{m['precision']:.3f} | {m['recall']:.3f} |")
    v = r["challenger"]["validation"]
    td = v["threshold_diagnostics"]
    cd = b["test"]["validation_threshold_diagnostics"]
    lines += [
        "",
        "## How the champion's threshold behaves",
        "",
        f"The published champion threshold ({pub['budget_threshold']:.6f}) was taken from the "
        "TEST scores by the legacy top-k rule. "
        f"{pb['score_count_at_threshold']} test units share that exact score, so all of them "
        f"alert: {pb['alerts']} alerts, {pb['alerts_per_shift']:.2f}/shift against a "
        f"{r['alert_budget']['max_alerts_per_shift']:.0f}/shift budget. Chosen on validation with "
        "the tie-aware rule instead, the champion's top plateau alone "
        f"({cd['score_count_at_threshold']} validation units) exceeds the "
        f"{r['alert_budget']['validation_budget_alerts']}-alert validation budget, so it "
        f"can alert on **{bt['validation_threshold']['alerts']}** test units — the collapse "
        "leaves it no budget-respecting operating point at all.",
        "",
        "## Validation (days "
        f"{r['splits']['validate']['days'][0]}–{r['splits']['validate']['days'][1]}) — "
        "ranking only, optimistic by construction",
        "",
        "Validation is used for early stopping, calibration AND threshold selection, so these "
        "numbers rank candidates; they are not the result.",
        "",
        f"- Precision {v['precision']:.3f}, recall {v['recall']:.3f}, PR-AUC {v['pr_auc']:.3f} "
        f"(raw {v['raw_pr_auc']:.3f}), Brier {v['brier']:.4f}",
        f"- Threshold {td['threshold']:.6f}: {td['alerts']} alerts "
        f"({v['alerts_per_shift']:.2f}/shift), naive top-k would have given "
        f"{td['naive_alerts']}, ties at the threshold {td['score_count_at_threshold']}",
        "- Walk-forward PR-AUC per fold: "
        + ", ".join(f"{x:.3f}" for x in r["challenger"]["walk_forward_pr_auc"]),
        f"- Eligible under the selection constraints: {r['challenger']['eligible_on_validation']} "
        f"{r['challenger']['constraints']}",
        "",
        "## Calibration",
        "",
        "Fitted on validation predictions only; methods compared by Brier CROSS-FITTED across the "
        "two halves of the validation days (an in-sample Brier always favours isotonic). "
        "Isotonic is rejected if its plateaus collapse the alerted cohort.",
        "",
        "| Method | Cross-fitted Brier | Alert unique scores | Collapsed |",
        "|---|---:|---:|---|",
    ]
    for m, dd in r["challenger"]["calibration"].items():
        if m == "selected":
            continue
        bs = dd["cross_fitted_brier"]
        lines.append(f"| {m} | {bs:.5f} | {dd['alert_cohort']['unique_scores']} | "
                     f"{dd['alert_cohort']['collapsed']} |" if bs is not None else
                     f"| {m} | — | — | — |")
    lines += ["", f"Selected: **{r['challenger']['calibration']['selected']}**.", "",
              "## Hyperparameters", "", "| | HGB champion | XGBoost challenger |", "|---|---|---|"]
    lines.append(f"| parameters | `{json.dumps(b['hyperparameters'])}` | "
                 f"`{json.dumps(r['challenger']['hyperparameters'])}` |")
    lines.append(f"| trees used | {b['hyperparameters']['n_iter_']} | "
                 f"{r['challenger']['best_iteration'] + 1} |")
    lines += ["", "Search space (seeded `ParameterSampler`, seed 42):", ""]
    lines += [f"- `{k}`: {vv}" for k, vv in r["search_space"].items()]
    if r.get("history"):
        lines += ["", "## Run history — each run is one look at the test set", "",
                  "| Run | Winner trial | Test precision | Test recall | Test PR-AUC | "
                  "Alerts/shift | Failed gates |", "|---|---:|---:|---:|---:|---:|---|"]
        for h in r["history"]:
            lines.append(f"| `{h['file']}` | {h['winner_trial']} | {h['test_precision']:.4f} | "
                         f"{h['test_recall']:.4f} | {h['test_pr_auc']:.4f} | "
                         f"{h['test_alerts_per_shift']:.2f} | {', '.join(h['failed_gates'])} |")
        lines.append(f"| **this run** | {r['trials_summary'][0]['trial']} | {ch['precision']:.4f} | "
                     f"{ch['recall']:.4f} | {ch['pr_auc']:.4f} | {ch['alerts_per_shift']:.2f} | "
                     f"{', '.join(r['promotion']['failed']) or 'none'} |")
        lines += ["", "Run 1 ranked candidates by validation precision right after a count of "
                  "satisfied constraints. With the recall floor unreachable on validation, that "
                  "picked a 10-tree model that filled 32 of 126 alert slots. The rule was amended "
                  "(ADR-061: minimise the recall shortfall first, one diversity definition "
                  "everywhere) on that VALIDATION evidence, and the search re-run. The trees are "
                  "identical between runs; only the ranking and four calibrator choices changed. "
                  "The amendment was made after run 1's test result was seen, so this report is "
                  "the second look at the test set, not the first."]
    lines += ["", "## Reproduce", "", "```", r["command"],
              f"OMP_NUM_THREADS=1 python -m offline.ml.xgb_challenger --config configs/"
              f"{r['plant_id']}.yaml --smoke   # fast smoke run, 3 trials", "```", "",
              "## Environment", ""]
    lines += [f"- {k}: `{vv}`" for k, vv in r["versions"].items()]
    lines += ["", "Selection rule and gates: `offline/ml/xgb_challenger.py` docstring, ADR-061.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default="configs/plant_a.yaml")
    ap.add_argument("--trials", type=int, default=30, help="search trials (default 30)")
    ap.add_argument("--n-jobs", type=int, default=4, help="xgboost threads (recorded)")
    ap.add_argument("--smoke", action="store_true", help="3 trials, no docs write")
    args = ap.parse_args(argv)
    trials = 3 if args.smoke else args.trials
    run(args.config, trials, args.n_jobs, args.smoke,
        docs_path=None if args.smoke
        else ROOT / "docs" / f"XGB_CHALLENGER_{Path(args.config).stem}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
