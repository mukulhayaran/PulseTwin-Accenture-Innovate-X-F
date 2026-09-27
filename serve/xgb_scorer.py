"""Pure-numpy scorer for an exported XGBoost booster (ADR-061).

THE IMPORT WALL (CLAUDE.md §2). numpy, json and stdlib only -- xgboost is not, and never
will be, importable here. The challenger pipeline (offline/ml/xgb_challenger.py) writes
XGBoost's OWN JSON model format, truncated to the early-stopping iteration, inside a
`pulsetwin-xgb-v1` envelope that also carries the calibrator and the alert threshold.
This module walks those trees.

XGBoost semantics mirrored exactly (tests/test_xgb_challenger.py checks parity against
xgboost's own predict to 1e-5):
  * a node is a leaf when left_children == -1; its value is split_conditions[node];
  * a present value goes LEFT when  x < split_condition  (strict), compared in float32,
    because XGBoost stores both the thresholds and the input matrix as float32;
  * a missing value (NaN) follows default_left;
  * the margin starts at logit(base_score) for binary:logistic.

Same public surface as serve.scorer.Scorer, so serve/main.py treats both identically:
raw_score / predict_raw_proba / predict / explain / vector_from / feature_names.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FORMAT = "pulsetwin-xgb-v1"


class XGBScorer:
    model_type = "xgboost"

    def __init__(self, path: str | Path) -> None:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if doc.get("format") != FORMAT:
            raise ValueError(f"not a {FORMAT} artifact: {path}")
        learner = doc["booster"]["learner"]
        if learner["objective"]["name"] != "binary:logistic":
            raise ValueError("only binary:logistic boosters are supported")
        base = float(str(learner["learner_model_param"]["base_score"]).strip("[]"))
        self.base_margin = float(np.log(base / (1.0 - base)))
        self.feature_names = [str(n) for n in doc["feature_names"]]
        self._index = {n: i for i, n in enumerate(self.feature_names)}
        self.calibration = doc["calibration"]
        self.calibration_method = str(self.calibration["method"])
        self.alert_threshold = float(doc["alert_threshold"])
        self.model_version = str(doc.get("model_version", ""))
        # ADR-062: the model's own alert policy; None means the static alert_threshold.
        self.threshold_policy = doc.get("threshold_policy")
        self.trees = []
        for t in learner["gradient_booster"]["model"]["trees"]:
            if any(int(s) != 0 for s in t["split_type"]):
                raise ValueError("categorical splits are not supported by this scorer")
            left = np.asarray(t["left_children"], dtype=np.int64)
            self.trees.append({
                "left": left,
                "right": np.asarray(t["right_children"], dtype=np.int64),
                "feature": np.asarray(t["split_indices"], dtype=np.int64),
                "cond": np.asarray(t["split_conditions"], dtype=np.float32),
                "default_left": np.asarray(t["default_left"], dtype=bool),
                "cover": np.asarray(t["sum_hessian"], dtype=np.float64),
                "is_leaf": left == -1,
            })
        self.n_trees = len(self.trees)
        self._sv = [self._subtree_values(t) for t in self.trees]
        # Contributions telescope from each tree's root expectation to its leaf, so they
        # sum to raw_score - expected_value (the same contract as serve.scorer.Scorer).
        self.expected_value = self.base_margin + sum(float(sv[0]) for sv in self._sv)

    # ------------------------------------------------------------------ scoring

    def _leaves(self, t: dict, X32: np.ndarray) -> np.ndarray:
        node = np.zeros(len(X32), dtype=np.int64)
        active = ~t["is_leaf"][node]
        while active.any():
            idx = np.nonzero(active)[0]
            n = node[idx]
            v = X32[idx, t["feature"][n]]
            go_left = np.where(np.isnan(v), t["default_left"][n], v < t["cond"][n])
            node[idx] = np.where(go_left, t["left"][n], t["right"][n])
            active = ~t["is_leaf"][node]
        return node

    def raw_score(self, X: np.ndarray) -> np.ndarray:
        """The margin (log-odds) before any calibration."""
        X32 = np.atleast_2d(np.asarray(X, dtype=np.float64)).astype(np.float32)
        out = np.full(len(X32), self.base_margin, dtype=np.float64)
        for t in self.trees:
            out += t["cond"][self._leaves(t, X32)].astype(np.float64)
        return out

    def predict_raw_proba(self, X: np.ndarray) -> np.ndarray:
        """The model's raw score as a probability -- a DIAGNOSTIC ranking signal, not the
        probability of a defect. Supervisors are shown `predict`, never this."""
        return _sigmoid(self.raw_score(X))

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Calibrated defect probability (fitted on validation data only)."""
        return self.calibrate(self.predict_raw_proba(X))

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        c = self.calibration
        raw = np.asarray(raw, dtype=np.float64)
        if self.calibration_method == "platt":
            z = np.log(np.clip(raw, 1e-7, 1 - 1e-7) / (1 - np.clip(raw, 1e-7, 1 - 1e-7)))
            return _sigmoid(float(c["a"]) * z + float(c["b"]))
        if self.calibration_method == "isotonic":
            return np.clip(np.interp(raw, np.asarray(c["x"]), np.asarray(c["y"])), 0.0, 1.0)
        raise ValueError(f"unknown calibration method '{self.calibration_method}'")

    # ------------------------------------------------------------- explanations

    def _subtree_values(self, t: dict) -> np.ndarray:
        """Cover-weighted expected leaf value beneath every node reachable from the root.
        Post-order from the root, so it assumes nothing about node numbering (pruning can
        leave ids out of parent-before-child order)."""
        sv = np.zeros(len(t["left"]))
        stack, done = [0], set()
        while stack:
            n = stack[-1]
            if t["is_leaf"][n]:
                sv[n] = float(t["cond"][n])
                done.add(n)
                stack.pop()
                continue
            lft, rgt = int(t["left"][n]), int(t["right"][n])
            pending = [c for c in (lft, rgt) if c not in done]
            if pending:
                stack.extend(pending)
                continue
            wl, wr = t["cover"][lft], t["cover"][rgt]
            tot = wl + wr
            sv[n] = (wl * sv[lft] + wr * sv[rgt]) / tot if tot > 0 else 0.0
            done.add(n)
            stack.pop()
        return sv

    def explain(self, x: np.ndarray, top: int = 5) -> list[dict]:
        """Exact additive decision-path contributions in log-odds (not Shapley)."""
        x = np.asarray(x, dtype=np.float64).ravel()
        x32 = x.astype(np.float32)
        contrib = np.zeros(len(self.feature_names))
        for t, sv in zip(self.trees, self._sv):
            node = 0
            while not t["is_leaf"][node]:
                f = int(t["feature"][node])
                v = x32[f]
                left = t["default_left"][node] if np.isnan(v) else v < t["cond"][node]
                nxt = int(t["left"][node] if left else t["right"][node])
                contrib[f] += sv[nxt] - sv[node]
                node = nxt
        order = np.argsort(-np.abs(contrib))[:top]
        return [{"feature": self.feature_names[i], "contribution": float(contrib[i]),
                 "direction": "high" if contrib[i] > 0 else "low",
                 "value": float(x[i]) if not np.isnan(x[i]) else None}
                for i in order if abs(contrib[i]) > 1e-12]

    def vector_from(self, values: dict[str, float]) -> np.ndarray:
        x = np.full(len(self.feature_names), np.nan)
        for k, v in values.items():
            i = self._index.get(k)
            if i is not None and v is not None:
                x[i] = float(v)
        return x


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))
