"""Pure-numpy tree-ensemble scorer.

THE IMPORT WALL (CLAUDE.md §2). This module may import numpy and stdlib only -- no
sklearn, no xgboost, no shap. The trained ensemble arrives as flat arrays in
model_<plant>.npz and is walked here.

Correctness is proven by tests/test_scorer_parity.py: the entire test set is scored with
both sklearn and this scorer, and max |difference| must be under 1e-6.

EXPLANATIONS. `explain()` implements exact per-feature contributions by the
"saabas"/decision-path decomposition: walking a unit down each tree and attributing the
change in node value to whichever feature made the split. For a tree ensemble this is
exact and additive -- contributions sum to (raw score - expected_value) -- and it needs no
library. It is NOT full Shapley: it does not average over feature orderings, so
correlated features share credit by tree structure rather than symmetrically. The API
reports `explanation_mode: "decision_path"` so the claim on screen matches the maths
(docs/AUDIT.md M15 -- never call something SHAP that is not SHAP).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class Scorer:
    """One plant's ensemble. Load once at startup, score per request."""

    model_type = "hist_gradient_boosting"
    calibration_method = "platt"

    def __init__(self, path: str | Path) -> None:
        z = np.load(path, allow_pickle=True)
        self.feature_idx = z["feature_idx"]
        self.threshold = z["threshold"]
        self.left = z["left"]
        self.right = z["right"]
        self.leaf_value = z["leaf_value"]
        self.is_leaf = z["is_leaf"]
        self.missing_go_left = z["missing_go_left"]
        self.node_count = z["node_count"] if "node_count" in z else None
        self.tree_offsets = z["tree_offsets"]
        self.baseline = float(z["baseline"][0])
        self.platt_coef = float(z["platt_coef"][0])
        self.platt_intercept = float(z["platt_intercept"][0])
        self.feature_names = [str(s) for s in z["feature_names"]]
        self.n_trees = len(self.tree_offsets) - 1
        self._index = {n: i for i, n in enumerate(self.feature_names)}
        self._sv_cache: dict[int, float] = {}
        # The additive baseline explanations are measured against: the ensemble baseline
        # plus every tree's root value. Contributions along a decision path telescope to
        # (leaf - root), so they sum to raw_score - expected_value, NOT raw_score -
        # baseline. Naming this explicitly is what makes the additivity claim checkable.
        self.expected_value = self.baseline + sum(
            self._subtree_value(int(self.tree_offsets[t])) for t in range(self.n_trees)
        )

    # ------------------------------------------------------------------ traversal

    def _leaf_for(self, x: np.ndarray, root: int) -> int:
        node = root
        while not self.is_leaf[node]:
            f = self.feature_idx[node]
            v = x[f]
            if np.isnan(v):
                node = self.left[node] if self.missing_go_left[node] else self.right[node]
            else:
                node = self.left[node] if v <= self.threshold[node] else self.right[node]
        return int(node)

    def raw_score(self, X: np.ndarray) -> np.ndarray:
        """Sum of leaf values across all trees, plus the ensemble baseline (log-odds)."""
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        out = np.full(len(X), self.baseline)
        for i in range(len(X)):
            total = 0.0
            for t in range(self.n_trees):
                total += self.leaf_value[self._leaf_for(X[i], int(self.tree_offsets[t]))]
            out[i] += total
        return out

    def predict_raw_proba(self, X: np.ndarray) -> np.ndarray:
        """Uncalibrated probability -- the sigmoid of the raw ensemble score."""
        return _sigmoid(self.raw_score(X))

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Calibrated probability: Platt applied to the uncalibrated probability.

        Must mirror the offline pipeline exactly -- Platt was fitted on
        clf.predict_proba(...)[:, 1], not on the raw log-odds.
        """
        p = self.predict_raw_proba(X)
        return _sigmoid(self.platt_coef * p + self.platt_intercept)

    # --------------------------------------------------------------- explanations

    def explain(self, x: np.ndarray, top: int = 5) -> list[dict]:
        """Exact additive per-feature contributions in log-odds space.

        Walks each tree and credits every split to the feature that made it. The sum of
        all contributions equals raw_score(x) - expected_value, exactly.
        """
        x = np.asarray(x, dtype=np.float64).ravel()
        contrib = np.zeros(len(self.feature_names))

        for t in range(self.n_trees):
            node = int(self.tree_offsets[t])
            parent_value = self._subtree_value(node)
            while not self.is_leaf[node]:
                f = int(self.feature_idx[node])
                v = x[f]
                if np.isnan(v):
                    nxt = self.left[node] if self.missing_go_left[node] else self.right[node]
                else:
                    nxt = self.left[node] if v <= self.threshold[node] else self.right[node]
                child_value = self._subtree_value(int(nxt))
                contrib[f] += child_value - parent_value
                parent_value = child_value
                node = int(nxt)

        order = np.argsort(-np.abs(contrib))[:top]
        return [
            {
                "feature": self.feature_names[i],
                "contribution": float(contrib[i]),
                "direction": "high" if contrib[i] > 0 else "low",
                "value": float(x[i]) if not np.isnan(x[i]) else None,
            }
            for i in order
            if abs(contrib[i]) > 1e-12
        ]

    def _subtree_value(self, node: int) -> float:
        """Expected leaf value beneath a node, weighted by training sample counts.

        An unweighted 0.5/0.5 average badly misattributes on unbalanced trees -- a split
        sending 2% of samples one way would be credited as though it sent half. Weighting
        by node counts makes this the genuine Saabas decomposition.
        """
        hit = self._sv_cache.get(node)
        if hit is not None:
            return hit
        if self.is_leaf[node]:
            val = float(self.leaf_value[node])
        else:
            l, r = int(self.left[node]), int(self.right[node])
            if self.node_count is not None:
                wl, wr = float(self.node_count[l]), float(self.node_count[r])
                tot = wl + wr
                val = ((wl * self._subtree_value(l) + wr * self._subtree_value(r)) / tot
                       if tot > 0 else 0.0)
            else:
                val = 0.5 * (self._subtree_value(l) + self._subtree_value(r))
        self._sv_cache[node] = val
        return val

    # -------------------------------------------------------------------- helpers

    def vector_from(self, values: dict[str, float]) -> np.ndarray:
        """Build a full feature vector from a partial dict; unknown features stay NaN.

        NaN is meaningful to this ensemble -- it was trained with native missing-value
        handling, so an absent feature routes down the branch the training data chose,
        rather than being silently imputed to zero.
        """
        x = np.full(len(self.feature_names), np.nan)
        for k, v in values.items():
            i = self._index.get(k)
            if i is not None and v is not None:
                x[i] = float(v)
        return x


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))

