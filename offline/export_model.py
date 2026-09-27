"""Export a trained sklearn ensemble to flat numpy arrays.

This is the bridge across the import wall. `serve/` may not import sklearn, so the tree
ensemble is flattened into plain arrays that a ~90-line numpy scorer can walk, and the
Platt calibration reduces to two floats (ADR-021 -- one of the reasons Platt beat isotonic
here: isotonic would have needed two step-function arrays).

Correctness is proven, not asserted: `tests/test_scorer_parity.py` scores the entire test
set with both sklearn and the numpy scorer and requires max |difference| < 1e-6.

Per-plant output (ADR-006): model_plant_a.npz, model_plant_b.npz.
"""

from __future__ import annotations

import numpy as np


def flatten_ensemble(clf) -> dict[str, np.ndarray]:
    """Flatten HistGradientBoostingClassifier predictors into contiguous arrays.

    sklearn stores each tree as a struct array of nodes. We emit, per node:
        feature_idx, threshold, left, right, value, is_leaf, missing_go_left
    with `tree_offsets` marking where each tree starts.
    """
    feature_idx: list[int] = []
    threshold: list[float] = []
    left: list[int] = []
    right: list[int] = []
    value: list[float] = []
    is_leaf: list[int] = []
    missing_left: list[int] = []
    count: list[float] = []
    offsets: list[int] = [0]

    for stage in clf._predictors:
        for predictor in stage:
            nodes = predictor.nodes
            base = len(feature_idx)
            for n in nodes:
                leaf = bool(n["is_leaf"])
                is_leaf.append(1 if leaf else 0)
                feature_idx.append(-1 if leaf else int(n["feature_idx"]))
                threshold.append(0.0 if leaf else float(n["num_threshold"]))
                left.append(-1 if leaf else base + int(n["left"]))
                right.append(-1 if leaf else base + int(n["right"]))
                value.append(float(n["value"]) if leaf else 0.0)
                missing_left.append(0 if leaf else int(n["missing_go_to_left"]))
                count.append(float(n["count"]))
            offsets.append(len(feature_idx))

    return {
        "feature_idx": np.asarray(feature_idx, dtype=np.int32),
        "threshold": np.asarray(threshold, dtype=np.float64),
        "left": np.asarray(left, dtype=np.int32),
        "right": np.asarray(right, dtype=np.int32),
        "leaf_value": np.asarray(value, dtype=np.float64),
        "is_leaf": np.asarray(is_leaf, dtype=np.int8),
        "missing_go_left": np.asarray(missing_left, dtype=np.int8),
        "node_count": np.asarray(count, dtype=np.float64),
        "tree_offsets": np.asarray(offsets, dtype=np.int32),
        "baseline": np.asarray([float(np.ravel(clf._baseline_prediction)[0])], dtype=np.float64),
    }


def export(model, path, feature_names: list[str], extra: dict | None = None) -> dict:
    arrays = flatten_ensemble(model.clf)
    arrays["platt_coef"] = np.asarray([float(model.platt.coef_.ravel()[0])], dtype=np.float64)
    arrays["platt_intercept"] = np.asarray(
        [float(model.platt.intercept_.ravel()[0])], dtype=np.float64
    )
    arrays["feature_names"] = np.asarray(feature_names, dtype=object)
    if extra:
        for k, v in extra.items():
            arrays[k] = np.asarray(v)
    np.savez_compressed(path, **arrays)
    return arrays
