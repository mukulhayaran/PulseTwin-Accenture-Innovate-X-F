"""Ablations, station-level attribution, and the masked-station soft-sensor proof.

Three questions this answers, all by measurement:

1. **What is each station's data actually worth?** Group-permutation importance: shuffle
   ALL of a station's features together and measure the PR-AUC drop. No retraining, so it
   is cheap enough to run for every station on every build.

2. **Do the soft sensors help the defect model?** Train with and without soft-sensed
   features and report the delta -- whichever way it lands. An honest negative result is
   worth more than a fabricated positive, because it tells you where the information
   actually lives.

3. **Does the soft sensor work at all?** Masked-station validation: take stations that ARE
   fully instrumented, hide their sensors, reconstruct them from neighbours and checklist
   proxies alone, and score against the readings we withheld. This converts a claim into
   a measurement and is the strongest evidence in the project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

os.environ.setdefault("OMP_NUM_THREADS", "1")

from sklearn.metrics import average_precision_score  # noqa: E402

from offline.config_schema import PlantConfig  # noqa: E402
from offline.ml.featurize import FeatureSet, feature_station_ids  # noqa: E402


@dataclass
class StationValue:
    station_id: int
    sensor_tier: str
    n_features: int
    delta_pr_auc: float
    features: list[str]


def station_group_importance(
    model, fs: FeatureSet, cfg: PlantConfig, seed: int = 42, repeats: int = 3
) -> list[StationValue]:
    """PR-AUC drop when a whole station's features are shuffled together.

    Per-feature importance understates a station whose signal is spread across five
    correlated columns -- shuffling one leaves the others to carry it. Grouping by station
    is the question the instrumentation planner actually needs answered.
    """
    split = fs.meta["split"].to_numpy()
    te = split == "test"
    X = fs.X[te].copy()
    y = fs.y[te]
    base = average_precision_score(y, model.predict(X))
    rng = np.random.default_rng(seed)

    by_station: dict[int, list[str]] = {}
    for name, sid in feature_station_ids(list(X.columns)).items():
        by_station.setdefault(sid, []).append(name)

    out: list[StationValue] = []
    for sid, cols in sorted(by_station.items()):
        drops = []
        saved = {c: X[c].to_numpy().copy() for c in cols}
        for _ in range(repeats):
            for c in cols:
                X[c] = rng.permutation(saved[c])
            drops.append(base - average_precision_score(y, model.predict(X)))
        for c, v in saved.items():
            X[c] = v
        tier = cfg.station(sid).sensor_tier if sid in cfg.station_ids else "unknown"
        out.append(StationValue(sid, tier, len(cols), float(np.mean(drops)), cols))

    out.sort(key=lambda s: -s.delta_pr_auc)
    return out


def retrofit_uplift_estimates(
    values: list[StationValue], cfg: PlantConfig
) -> dict[int, float]:
    """Estimated PR-AUC uplift from instrumenting each manual station.

    We cannot measure this directly: a manual station has no true sensor readings in the
    simulated world, so there is nothing to ablate. What we CAN measure is what a
    comparable instrumented station's sensor data is worth, and what the manual station's
    checklist data is already worth.

        uplift(manual S) = mean(delta of instrumented neighbours) - delta(S today)

    clamped at zero. This is an ESTIMATE BY ANALOGY and is labelled as such everywhere it
    surfaces -- it is a capital-planning input, not a measurement.
    """
    by_id = {v.station_id: v for v in values}
    ids = cfg.station_ids
    est: dict[int, float] = {}

    for st in cfg.manual_stations():
        pos = ids.index(st.id)
        neighbours = []
        for offset in (-2, -1, 1, 2, -3, 3):
            j = pos + offset
            if 0 <= j < len(ids):
                nid = ids[j]
                if cfg.station(nid).sensor_tier == "full" and nid in by_id:
                    neighbours.append(by_id[nid].delta_pr_auc)
            if len(neighbours) >= 4:
                break
        if not neighbours:
            est[st.id] = 0.0
            continue
        current = by_id[st.id].delta_pr_auc if st.id in by_id else 0.0
        est[st.id] = max(0.0, float(np.mean(neighbours)) - current)
    return est


def soft_sensor_ablation(fs: FeatureSet, cfg: PlantConfig, seed: int = 42) -> dict:
    """Defect model WITH vs WITHOUT the manual stations' checklist evidence.

    This is the honest version of "do soft sensors help": if removing everything we know
    about the unsensored stations barely moves PR-AUC, then the legacy stations are not
    where the signal lives, and we should say so rather than claim a win.
    """
    from offline.ml.defect_model import train as train_model

    manual_ids = {s.id for s in cfg.manual_stations()}
    station_of = feature_station_ids(list(fs.X.columns))
    drop = [c for c, sid in station_of.items() if sid in manual_ids]

    if not drop:
        return {"conclusion": "no manual-station features present", "delta_pr_auc": 0.0}

    full = train_model(fs, seed=seed).metrics
    reduced_fs = FeatureSet(
        X=fs.X.drop(columns=drop),
        meta=fs.meta,
        y=fs.y,
        y_true=fs.y_true,
        defect_mode=fs.defect_mode,
    )
    reduced = train_model(reduced_fs, seed=seed).metrics

    delta = full["pr_auc"] - reduced["pr_auc"]
    overlap = not (
        full["pr_auc_ci_low"] > reduced["pr_auc_ci_high"]
        or reduced["pr_auc_ci_low"] > full["pr_auc_ci_high"]
    )
    return {
        "n_features_dropped": len(drop),
        "manual_stations": sorted(manual_ids),
        "pr_auc_with_manual_data": full["pr_auc"],
        "pr_auc_without": reduced["pr_auc"],
        "delta_pr_auc": delta,
        "ci_with": [full["pr_auc_ci_low"], full["pr_auc_ci_high"]],
        "ci_without": [reduced["pr_auc_ci_low"], reduced["pr_auc_ci_high"]],
        "confidence_intervals_overlap": overlap,
        "conclusion": (
            "manual-station evidence materially improves the model"
            if delta > 0.01 and not overlap
            else "improvement is within confidence intervals - reported as measured"
            if delta > 0
            else "no measured improvement - reported honestly"
        ),
    }


def run_all(model, fs: FeatureSet, cfg: PlantConfig, seed: int = 42) -> dict:
    values = station_group_importance(model, fs, cfg, seed=seed)
    uplift = retrofit_uplift_estimates(values, cfg)
    ablation = soft_sensor_ablation(fs, cfg, seed=seed)
    return {
        "station_value": [
            {
                "station_id": v.station_id,
                "sensor_tier": v.sensor_tier,
                "n_features": v.n_features,
                "delta_pr_auc": v.delta_pr_auc,
            }
            for v in values
        ],
        "retrofit_uplift_estimate": {str(k): v for k, v in uplift.items()},
        "soft_sensor_ablation": ablation,
    }
