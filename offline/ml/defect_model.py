"""Defect risk model: gradient-boosted trees + Platt calibration + honest metrics.

ADR-021 -- Platt, not isotonic. The validation fold holds a few hundred positives, far
too few for isotonic regression, which would fit that sample's noise into a coarse step
function and then look miscalibrated on test -- discrediting the very reliability diagram
it is meant to support. Platt also exports to two floats, which keeps the numpy scorer and
the .npz artifact simple.

ADR-020 -- every headline metric carries a bootstrap confidence interval. On a ~3% base
rate the test split holds a few hundred positives and PR-AUC has a CI around +/-0.05. The
spec's own control rule ("above 0.92 make the world harder, below make it easier") can
fire on pure sampling noise, so the band is advisory and the interval is always reported.

Class imbalance is handled with sample weights, never resampling: resampling rows that
carry trailing-window features would duplicate temporal context and leak.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

os.environ.setdefault("OMP_NUM_THREADS", "1")  # ADR-016, before sklearn is imported

from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import average_precision_score, roc_auc_score  # noqa: E402

from offline.ml.featurize import FeatureSet  # noqa: E402

N_BOOTSTRAP = 1000


@dataclass
class TrainedModel:
    clf: HistGradientBoostingClassifier
    platt: LogisticRegression
    feature_names: list[str]
    metrics: dict = field(default_factory=dict)

    def predict_raw(self, X) -> np.ndarray:
        return self.clf.predict_proba(X)[:, 1]

    def predict(self, X) -> np.ndarray:
        """Calibrated probability."""
        raw = self.predict_raw(X)
        return self.platt.predict_proba(raw.reshape(-1, 1))[:, 1]


def bootstrap_ci(
    y: np.ndarray, p: np.ndarray, fn, n: int = N_BOOTSTRAP, seed: int = 42
) -> tuple[float, float, float]:
    """Point estimate plus a percentile bootstrap 95% interval."""
    rng = np.random.default_rng(seed)
    point = float(fn(y, p))
    idx = np.arange(len(y))
    vals = []
    for _ in range(n):
        s = rng.choice(idx, size=len(idx), replace=True)
        if y[s].sum() == 0 or y[s].sum() == len(s):
            continue
        vals.append(fn(y[s], p[s]))
    if not vals:
        return point, point, point
    return point, float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def reliability_curve(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    """Decile reliability diagram with per-bin Wilson intervals."""
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    out = []
    for i in range(bins):
        m = (p > edges[i]) & (p <= edges[i + 1])
        n = int(m.sum())
        if n == 0:
            continue
        obs = float(y[m].mean())
        se = float(np.sqrt(max(obs * (1 - obs), 1e-9) / n))
        out.append(
            {
                "bin": i,
                "predicted": float(p[m].mean()),
                "observed": obs,
                "n": n,
                "ci_low": max(0.0, obs - 1.96 * se),
                "ci_high": min(1.0, obs + 1.96 * se),
            }
        )
    return out


DEFAULT_HYPERPARAMS = {
    "max_iter": 300,
    "learning_rate": 0.06,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
}


def train(fs: FeatureSet, seed: int = 42, hyperparams: dict | None = None) -> TrainedModel:
    """`hyperparams` overrides DEFAULT_HYPERPARAMS -- used by mlops/retrainer.py to train a
    genuinely different challenger from the same seed and the same data (CLAUDE.md's
    "seed 42 everywhere" rules out varying random_state to manufacture a difference,
    see docs/MLOPS_LIFECYCLE.md). Every existing caller passes None and gets byte-identical
    behaviour to before this parameter existed.
    """
    split = fs.meta["split"].to_numpy()
    tr, va, te = split == "train", split == "validate", split == "test"

    Xtr, ytr = fs.X[tr], fs.y[tr]
    Xva, yva = fs.X[va], fs.y[va]
    Xte, yte = fs.X[te], fs.y[te]

    # Cost-sensitive weighting rather than resampling (resampling + trailing features
    # duplicates temporal context and leaks).
    pos_weight = float((ytr == 0).sum() / max(1, (ytr == 1).sum()))
    w = np.where(ytr == 1, pos_weight, 1.0)

    hp = {**DEFAULT_HYPERPARAMS, **(hyperparams or {})}
    clf = HistGradientBoostingClassifier(
        max_iter=hp["max_iter"],
        learning_rate=hp["learning_rate"],
        max_leaf_nodes=hp["max_leaf_nodes"],
        min_samples_leaf=hp["min_samples_leaf"],
        l2_regularization=hp["l2_regularization"],
        early_stopping=True,
        validation_fraction=0.15,
        random_state=seed,
    )
    clf.fit(Xtr, ytr, sample_weight=w)

    # Platt calibration fitted on the validation fold only -- never on train (the model
    # is overconfident there) and never on test (that would be scoring on fitted data).
    raw_va = clf.predict_proba(Xva)[:, 1]
    platt = LogisticRegression(C=1e6, solver="lbfgs")
    platt.fit(raw_va.reshape(-1, 1), yva)

    model = TrainedModel(clf=clf, platt=platt, feature_names=list(fs.X.columns))

    p_te = model.predict(Xte)
    p_va = model.predict(Xva)

    pr, pr_lo, pr_hi = bootstrap_ci(yte, p_te, average_precision_score, seed=seed)
    roc, roc_lo, roc_hi = bootstrap_ci(yte, p_te, roc_auc_score, seed=seed)

    model.metrics = {
        "pr_auc": pr,
        "pr_auc_ci_low": pr_lo,
        "pr_auc_ci_high": pr_hi,
        "roc_auc": roc,
        "roc_auc_ci_low": roc_lo,
        "roc_auc_ci_high": roc_hi,
        "base_rate_test": float(yte.mean()),
        "positives_test": int(yte.sum()),
        "n_test": int(te.sum()),
        "n_train": int(tr.sum()),
        "n_validate": int(va.sum()),
        "pr_auc_validate": float(average_precision_score(yva, p_va)),
        "brier_test": float(np.mean((p_te - yte) ** 2)),
        "reliability": reliability_curve(yte, p_te),
        "pos_weight": pos_weight,
        "n_features": len(model.feature_names),
    }
    # A useful sanity anchor: PR-AUC of a random ranker is the base rate.
    model.metrics["pr_auc_lift_over_base"] = pr / max(1e-9, model.metrics["base_rate_test"])
    return model


def permutation_importance(
    model: TrainedModel, fs: FeatureSet, top: int = 25, seed: int = 42
) -> list[dict]:
    """Drop in PR-AUC when a feature is shuffled. Used for the root-cause Pareto."""
    split = fs.meta["split"].to_numpy()
    te = split == "test"
    X, y = fs.X[te].copy(), fs.y[te]
    base = average_precision_score(y, model.predict(X))
    rng = np.random.default_rng(seed)

    out = []
    for col in X.columns:
        if X[col].nunique(dropna=True) <= 1:
            continue
        saved = X[col].to_numpy().copy()
        X[col] = rng.permutation(saved)
        score = average_precision_score(y, model.predict(X))
        X[col] = saved
        out.append({"feature": col, "delta_pr_auc": float(base - score)})

    out.sort(key=lambda d: -d["delta_pr_auc"])
    return out[:top]
