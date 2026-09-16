"""Request-time SPC fallback. numpy + stdlib only (CLAUDE.md SS2 / ADR-033).

Loads the per-(station, signal) rolling baselines mlops/fallback.py exported offline
(artifacts/fallback_<plant>.npz) and applies the one SPC check cheap enough to run per
request without a unit-sequence history: the 3-sigma rule. The full 8-Nelson-rule battery
needs a CONSECUTIVE-UNITS time series and only ever runs offline
(mlops/fallback.py's evaluate_shadow), which is what /api/model/health's
`fallback.shadow_precision` / `shadow_recall` report.

Surfaced by /api/predict/defect when the primary scorer is unavailable or degraded --
the response is labelled `explanation_mode: "spc_fallback"` so the UI can render the
"FALLBACK MODE" badge (docs/MLOPS_LIFECYCLE.md) instead of quietly returning a number that
looks like the real model spoke.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

_FEATURE_RE = re.compile(r"^s(\d+)_(.+)$")


class FallbackScorer:
    def __init__(self, path: str | Path) -> None:
        z = np.load(path, allow_pickle=True)
        station_id = z["station_id"]
        signal = [str(s) for s in z["signal"]]
        self.mean = z["mean"]
        self.std = z["std"]
        self._index: dict[tuple[int, str], int] = {
            (int(sid), sig): i for i, (sid, sig) in enumerate(zip(station_id, signal))
        }

    def score(self, features: dict[str, float]) -> dict:
        violations: list[dict] = []
        considered = 0
        max_abs_z = 0.0

        for key, value in features.items():
            if value is None:
                continue
            m = _FEATURE_RE.match(key)
            if not m:
                continue
            sid, signal = int(m.group(1)), m.group(2)
            idx = self._index.get((sid, signal))
            if idx is None:
                continue
            considered += 1
            std = self.std[idx] or 1.0
            zscore = (float(value) - self.mean[idx]) / std
            max_abs_z = max(max_abs_z, abs(zscore))
            if abs(zscore) > 3.0:
                violations.append({
                    "station_id": sid, "signal": signal, "value": float(value),
                    "z_score": round(float(zscore), 2),
                    "baseline_mean": round(float(self.mean[idx]), 3),
                })

        if considered == 0:
            probability = 0.0
        else:
            violation_frac = len(violations) / considered
            # A single hard 3-sigma violation should already read as meaningfully risky;
            # blending the violation fraction with severity avoids needing many violating
            # signals before the score moves off zero.
            probability = float(min(1.0, 0.5 * violation_frac * 4.0 + 0.5 * min(1.0, max_abs_z / 6.0)))

        return {
            "probability": round(probability, 4),
            "violations": violations,
            "n_signals_considered": considered,
            "explanation_mode": "spc_fallback",
        }
