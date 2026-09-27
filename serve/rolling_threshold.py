"""Trailing-window alert-budget threshold (ADR-062).

A static threshold learned on one week lets the alert rate drift with the next week's
score distribution: the XGBoost challenger's validation threshold gave exactly 6.00
alerts/shift on validation and 8.81 on test. This keeps the cut tracking the budget: the
threshold is the tie-aware budget cut over the calibrated scores of the most recent
`window_units` scored units. It uses past SCORES only, never a label, so it cannot leak.

Stdlib + numpy (the import wall). `budget_cut` mirrors
offline/ml/xgb_challenger.py::select_threshold -- serve cannot import offline/ -- and
tests/test_xgb_challenger.py asserts the two agree.
"""

from __future__ import annotations

import threading
from collections import deque

import numpy as np


def budget_cut(p: np.ndarray, budget_alerts: int) -> float:
    """The lowest score whose admitted count (p >= score) is <= the budget. A tied block
    that would overrun the budget is excluded whole; if even the top score's block is too
    big, the cut sits above every score and nothing alerts."""
    p = np.asarray(p, dtype=np.float64)
    if len(p) == 0 or budget_alerts <= 0:
        return float("inf")
    values = np.unique(p)[::-1]
    counts = len(p) - np.searchsorted(np.sort(p), values, side="left")
    admissible = values[counts <= budget_alerts]
    if len(admissible) == 0:
        return float(values[0]) + 1e-12
    return float(admissible[-1])


class TrailingBudgetThreshold:
    """Per-plant, thread-safe: FastAPI runs sync endpoints on a thread pool."""

    def __init__(self, seed_scores, window_units: int, budget_alerts: int) -> None:
        self.window_units = int(window_units)
        self.budget_alerts = int(budget_alerts)
        self._scores: deque = deque((float(s) for s in seed_scores), maxlen=self.window_units)
        self._lock = threading.Lock()
        self._cached: float | None = None

    @property
    def threshold(self) -> float:
        with self._lock:
            if self._cached is None:
                self._cached = budget_cut(np.fromiter(self._scores, float), self.budget_alerts)
            return self._cached

    def __deepcopy__(self, memo):
        # The live twin is forked with copy.deepcopy (ADR-055); a Lock cannot be copied,
        # and a fork must get its OWN window, so copy the state, not the object.
        clone = TrailingBudgetThreshold(list(self._scores), self.window_units,
                                        self.budget_alerts)
        memo[id(self)] = clone
        return clone

    def observe(self, calibrated_risk: float) -> None:
        """One scored unit enters the window (in production each unit is scored once, at
        the prediction cutoff)."""
        with self._lock:
            self._scores.append(float(calibrated_risk))
            self._cached = None
