"""Train + evaluate a defect/failure model for a benchmark dataset.

Deliberately thin. `offline/ml/defect_model.py::train()` already implements exactly what
the source brief asked for -- HistGradientBoostingClassifier, Platt calibration fitted on
a held-out validation fold (never train, never test), cost-sensitive class weights instead
of resampling, and bootstrap-CI'd PR-AUC/ROC-AUC -- so this module's only job is to build
the `FeatureSet` shape that function expects from a benchmark adapter's (X, y, split) and
call it, rather than re-implementing gradient boosting + calibration a second time.

`FeatureSet.y_true` and `.defect_mode` are simulator-specific fields (the noisy observed
label vs. the simulator's own ground truth, and which competing-risk mode produced a
defect) that no public dataset here has an equivalent of. Set to harmless placeholders --
`train()`, `bootstrap_ci()` and `permutation_importance()` never read either field, only
`.X`, `.y` and `.meta["split"]` (confirmed by reading defect_model.py directly, not
assumed).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from offline.ml.defect_model import TrainedModel, permutation_importance, train
from offline.ml.featurize import FeatureSet


def build_feature_set(
    X_train: pd.DataFrame, y_train: np.ndarray,
    X_val: pd.DataFrame, y_val: np.ndarray,
    X_test: pd.DataFrame, y_test: np.ndarray,
) -> FeatureSet:
    X = pd.concat([X_train, X_val, X_test], axis=0, ignore_index=True)
    y = np.concatenate([y_train, y_val, y_test])
    split = np.array(
        ["train"] * len(y_train) + ["validate"] * len(y_val) + ["test"] * len(y_test))
    meta = pd.DataFrame({"split": split})
    return FeatureSet(
        X=X, meta=meta, y=y,
        y_true=y,                              # no separate ground-truth label exists
        defect_mode=np.array([""] * len(y), dtype=object),  # not applicable here
    )


def select_threshold(y_val: np.ndarray, p_val: np.ndarray) -> float:
    """The F1-maximizing threshold on VALIDATION only.

    §18 of the source brief: "Never tune the threshold on the final test set." Scanning
    100 candidate cuts against the val fold and picking the best is still a form of
    fitting, which is exactly why it must never touch test -- test is scored once, at
    this already-chosen cut, and reported honestly even if a different cut would have
    looked better in hindsight.
    """
    candidates = np.unique(np.clip(p_val, 1e-6, 1 - 1e-6))
    if len(candidates) == 0:
        return 0.5
    best_t, best_f1 = 0.5, -1.0
    for t in candidates:
        f1 = f1_score(y_val, (p_val >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_t, best_f1 = float(t), f1
    return best_t


def threshold_metrics(y_true: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    """Precision/recall/F1/confusion matrix at a FIXED, already-chosen threshold.
    Never accuracy alone (§18) -- it is not even computed here."""
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "threshold": threshold,
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "false_positive_rate": float(fp / max(1, fp + tn)),
        "false_alarm_rate": float(fp / max(1, fp + tp)),  # of all alarms, share that were false
        "confusion_matrix": {
            "true_negative": int(tn), "false_positive": int(fp),
            "false_negative": int(fn), "true_positive": int(tp),
        },
    }


def train_and_evaluate(
    fs: FeatureSet, *, seed: int = 42, hyperparams: dict | None = None,
) -> tuple[TrainedModel, list[dict], dict]:
    model = train(fs, seed=seed, hyperparams=hyperparams)
    importance = permutation_importance(model, fs, top=15, seed=seed)

    split = fs.meta["split"].to_numpy()
    va, te = split == "validate", split == "test"
    p_val = model.predict(fs.X[va])
    p_test = model.predict(fs.X[te])
    threshold = select_threshold(fs.y[va], p_val)
    threshold_result = threshold_metrics(fs.y[te], p_test, threshold)

    return model, importance, threshold_result
