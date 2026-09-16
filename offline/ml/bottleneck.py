"""Shifting-bottleneck detection and short-horizon forecasting.

DETECTION uses the Roser active-period criterion: the station with the largest active
share (working + down, i.e. not waiting on anyone else) in a window is the constraint.

GROUND TRUTH IS A DIFFERENT COMPUTATION ON PURPOSE. Scoring a 15-minute argmax against a
15-minute argmax is circular and returns 100%, which is worse than a low number because it
invites doubt about everything else. Ground truth here is the SUSTAINED constraint over a
2-hour smoothed window; the detector sees only a single 15-minute bucket, which is all a
live rolling window would have.

FORECASTING is benchmarked against persistence (predict the last value). A model that
cannot beat "assume nothing changes" on a 15-minute horizon has no business being shipped,
and the skill score says so plainly whichever way it lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from offline.config_schema import PlantConfig

GROUND_TRUTH_SMOOTH_BUCKETS = 8   # 2 hours at 15-minute resolution
FORECAST_HORIZON_BUCKETS = 1      # 15 minutes ahead
LAG_FEATURES = 4


@dataclass
class BottleneckReport:
    detection_accuracy: float
    n_windows: int
    shift_events: int
    transitions: list[dict] = field(default_factory=list)
    forecast_mae_model: float = 0.0
    forecast_mae_persistence: float = 0.0
    forecast_skill: float = 0.0
    station_share: dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "detection_accuracy": self.detection_accuracy,
            "n_windows": self.n_windows,
            "shift_events": self.shift_events,
            "forecast_mae_model": self.forecast_mae_model,
            "forecast_mae_persistence": self.forecast_mae_persistence,
            "forecast_skill": self.forecast_skill,
            "station_share": {str(k): v for k, v in self.station_share.items()},
            "transitions": self.transitions[:200],
        }


def active_share(secs: np.ndarray, working: int, down: int) -> np.ndarray:
    """(station, bucket) matrix of active seconds."""
    return secs[:, :, working] + secs[:, :, down]


def smooth(active: np.ndarray, k: int = GROUND_TRUTH_SMOOTH_BUCKETS) -> np.ndarray:
    kernel = np.ones(k) / k
    return np.vstack([np.convolve(active[j, :], kernel, mode="same") for j in range(active.shape[0])])


def detect(active: np.ndarray, ids: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Per-bucket detected constraint and its separation from the runner-up.

    Confidence is SEPARATION, not share of total: across 30 stations a winning share of
    0.038 sits barely above the 0.033 uniform value and reads as no confidence at all,
    even when the call is decisive.
    """
    n_buckets = active.shape[1]
    detected = np.full(n_buckets, -1, dtype=np.int32)
    confidence = np.zeros(n_buckets)

    for b in range(n_buckets):
        col = active[:, b]
        if col.sum() <= 0:
            continue
        order = np.argsort(-col)
        top = float(col[order[0]])
        second = float(col[order[1]]) if len(order) > 1 else 0.0
        detected[b] = ids[int(order[0])]
        confidence[b] = (top - second) / top if top > 0 else 0.0
    return detected, confidence


def ground_truth(active: np.ndarray, ids: list[int]) -> np.ndarray:
    sm = smooth(active)
    truth = np.where(active.sum(axis=0) > 0, np.array(ids)[np.argmax(sm, axis=0)], -1)
    return truth.astype(np.int32)


def forecast_skill(active: np.ndarray, ids: list[int], seed: int = 42) -> tuple[float, float, float]:
    """Predict each station's active share one bucket ahead; benchmark vs persistence.

    Ridge regression on lagged values, fitted on the first 70% of the horizon and scored
    on the last 30% -- a temporal split, like everything else in this project.
    """
    n_st, n_b = active.shape
    total = active.sum(axis=0, keepdims=True)
    share = np.divide(active, np.maximum(total, 1e-9))

    X_rows, y_rows, persist_rows = [], [], []
    for s in range(n_st):
        series = share[s]
        for t in range(LAG_FEATURES, n_b - FORECAST_HORIZON_BUCKETS):
            X_rows.append(series[t - LAG_FEATURES:t])
            y_rows.append(series[t + FORECAST_HORIZON_BUCKETS])
            persist_rows.append(series[t])

    if len(X_rows) < 200:
        return 0.0, 0.0, 0.0

    X = np.asarray(X_rows)
    y = np.asarray(y_rows)
    persistence = np.asarray(persist_rows)

    cut = int(0.7 * len(X))
    Xtr, ytr = X[:cut], y[:cut]
    Xte, yte = X[cut:], y[cut:]

    # Ridge in closed form -- no sklearn needed for a 4-feature regression.
    A = np.hstack([Xtr, np.ones((len(Xtr), 1))])
    lam = 1e-3
    w = np.linalg.solve(A.T @ A + lam * np.eye(A.shape[1]), A.T @ ytr)
    pred = np.hstack([Xte, np.ones((len(Xte), 1))]) @ w

    mae_model = float(np.mean(np.abs(pred - yte)))
    mae_persist = float(np.mean(np.abs(persistence[cut:] - yte)))
    skill = 1.0 - (mae_model / mae_persist) if mae_persist > 0 else 0.0
    return mae_model, mae_persist, skill


def analyse(
    secs: np.ndarray, ids: list[int], working_idx: int, down_idx: int, cfg: PlantConfig
) -> tuple[BottleneckReport, np.ndarray, np.ndarray, np.ndarray]:
    active = active_share(secs, working_idx, down_idx)
    detected, confidence = detect(active, ids)
    truth = ground_truth(active, ids)

    valid = (detected > 0) & (truth > 0)
    accuracy = float((detected[valid] == truth[valid]).mean()) if valid.any() else 0.0

    transitions = []
    prev = None
    shift_events = 0
    for b in range(len(detected)):
        if detected[b] <= 0:
            continue
        if prev is not None and detected[b] != prev:
            shift_events += 1
            transitions.append({
                "bucket": int(b),
                "from_station": int(prev),
                "to_station": int(detected[b]),
                "confidence": float(confidence[b]),
            })
        prev = int(detected[b])

    vals, counts = np.unique(detected[detected > 0], return_counts=True)
    share = {int(v): float(c / counts.sum()) for v, c in zip(vals, counts)}

    mae_m, mae_p, skill = forecast_skill(active, ids, seed=cfg.simulation.seed)

    report = BottleneckReport(
        detection_accuracy=accuracy,
        n_windows=int(valid.sum()),
        shift_events=shift_events,
        transitions=transitions,
        forecast_mae_model=mae_m,
        forecast_mae_persistence=mae_p,
        forecast_skill=skill,
        station_share=share,
    )
    return report, detected, confidence, truth
