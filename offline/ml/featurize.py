"""Station-level telemetry -> one row per unit, with leakage discipline.

THE CUTOFF. Only data available at or before the config-derived prediction station may be
used (S20 of 30 for plant A, S28 of 42 for plant B). Everything downstream of it is the
future at prediction time.

THE LEAKAGE TRAP THIS MODULE IS BUILT AROUND (docs/AUDIT.md H10). `lot_trailing_defect_rate`
is a legitimate feature -- a supervisor really can know how the last hundred units of this
lot inspected. But inspection happens at the END of the line, 25-40 minutes after the
prediction point. Selecting "the last 100 units of this lot" BY INDEX silently ingests
labels that do not exist yet. The leak is invisible in the output and inflates PR-AUC.

So the window is selected by `inspection_ts <= prediction_ts`, via merge_asof, never by
index. `tests/test_no_leakage.py` proves it by corrupting all post-cutoff data and
asserting the features do not move.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from feature_contract import (
    LOT_TRAILING_UNITS,
    RATE_OF_CHANGE_LAG,
    ROLLING_STD_UNITS,
    TRAILING_MEDIAN_UNITS,
    TRAILING_Z_UNITS,
)
from offline.config_schema import PlantConfig


@dataclass
class FeatureSet:
    X: pd.DataFrame              # numeric features only, one row per unit
    meta: pd.DataFrame           # unit_seq, unit_id, ts, split, variant, lot, shift
    y: np.ndarray                # defect_detected -- the noisy observed label
    y_true: np.ndarray           # simulator truth, OFFLINE VALIDATION ONLY
    defect_mode: np.ndarray

    @property
    def feature_names(self) -> list[str]:
        return list(self.X.columns)


def _split_for_day(cfg: PlantConfig, day: np.ndarray) -> np.ndarray:
    sim = cfg.simulation
    out = np.full(len(day), "none", dtype=object)
    out[(day >= sim.train_days[0]) & (day <= sim.train_days[1])] = "train"
    out[(day >= sim.validate_days[0]) & (day <= sim.validate_days[1])] = "validate"
    out[(day >= sim.test_days[0]) & (day <= sim.test_days[1])] = "test"
    return out


def _station_signal_features(
    cfg: PlantConfig, tel: pd.DataFrame, cutoff: int
) -> pd.DataFrame:
    """Per-station, per-signal features. All trailing windows exclude the current unit."""
    frames: list[pd.DataFrame] = []

    for st in cfg.stations:
        if st.id > cutoff or st.sensor_tier == "manual":
            continue
        rows = tel[tel["station_id"] == st.id].sort_values("entry_ts")
        if rows.empty:
            continue
        idx = rows["unit_seq"].to_numpy()
        block = {"unit_seq": idx}

        for sig in st.sensors:
            v = rows[sig]
            if v.isna().all():
                continue
            prefix = f"s{st.id}_{sig}"

            block[f"{prefix}"] = v.to_numpy()

            # shift(1) throughout: a unit must never contribute to its own baseline
            med = (
                v.groupby(rows["variant"].to_numpy())
                .transform(lambda s: s.shift(1).rolling(TRAILING_MEDIAN_UNITS, min_periods=8).median())
            )
            block[f"{prefix}_dev_median"] = (v - med).to_numpy()

            past = v.shift(1)
            mu = past.rolling(TRAILING_Z_UNITS, min_periods=20).mean()
            sd = past.rolling(TRAILING_Z_UNITS, min_periods=20).std()
            block[f"{prefix}_z"] = ((v - mu) / sd.replace(0, np.nan)).to_numpy()

            block[f"{prefix}_roc"] = (v - v.shift(RATE_OF_CHANGE_LAG)).to_numpy()
            block[f"{prefix}_roll_std"] = (
                past.rolling(ROLLING_STD_UNITS, min_periods=4).std().to_numpy()
            )

        frames.append(pd.DataFrame(block))

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="unit_seq", how="outer")
    return out


def _flow_features(cfg: PlantConfig, tel: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    """Sequence and buffer-flow features -- where Mode A's evidence lives."""
    up = tel[tel["station_id"] <= cutoff]
    g = up.groupby("unit_seq")

    out = pd.DataFrame(
        {
            "total_dwell_s": g["exit_ts"].max() - g["entry_ts"].min(),
            "total_blocked_s": g["blocked_before_s"].sum(),
            "total_buffer_wait_s": g["buffer_wait_s"].sum(),
            "max_station_wait_s": g["buffer_wait_s"].max(),
            "stations_blocked_count": g["prior_state"].apply(lambda s: (s == "blocked").sum()),
            "stations_starved_count": g["prior_state"].apply(lambda s: (s == "starved").sum()),
            "mean_cycle_s": g["cycle_s"].mean(),
            "max_cycle_s": g["cycle_s"].max(),
        }
    )

    # THE Mode A feature: time between leaving the primer and entering base coat,
    # spanning the unsensored prep station.
    w = cfg.defect_physics.mode_a.dwell_window
    if w.to_station <= cutoff:
        a = tel[tel["station_id"] == w.from_station].set_index("unit_seq")[
            "exit_ts" if w.from_edge == "exit" else "entry_ts"
        ]
        b = tel[tel["station_id"] == w.to_station].set_index("unit_seq")[
            "entry_ts" if w.to_edge == "entry" else "exit_ts"
        ]
        out["paint_buffer_dwell_s"] = (b - a).reindex(out.index)
        nominal = cfg.defect_physics.mode_a.dwell_nominal_s
        out["paint_buffer_dwell_ratio"] = out["paint_buffer_dwell_s"] / nominal

    # Ambient at each zone entry -- humidity is Mode A's second required condition.
    for zone in cfg.zones:
        z_stations = [s for s in zone.stations if s <= cutoff]
        if not z_stations:
            continue
        first = tel[tel["station_id"] == min(z_stations)].set_index("unit_seq")
        out[f"{zone.id}_entry_humidity_pct"] = first["ambient_humidity_pct"].reindex(out.index)
        out[f"{zone.id}_entry_temperature_c"] = first["ambient_temperature_c"].reindex(out.index)

    return out.reset_index()


def _checklist_features(
    cfg: PlantConfig, checklists: pd.DataFrame, cutoff: int
) -> pd.DataFrame:
    """Manual-station records -- the ONLY evidence for Mode B.

    Operator identity is hashed into buckets here and never leaves this module in raw
    form (CLAUDE.md §3 / ADR-017).
    """
    ck = checklists[checklists["station_id"] <= cutoff].copy()
    n_buckets = cfg.operators.operator_bucket_count
    ck["operator_bucket"] = (
        pd.util.hash_pandas_object(ck["operator_id"], index=False) % n_buckets
    ).astype(int)

    frames = []
    for sid in sorted(ck["station_id"].unique()):
        rows = ck[ck["station_id"] == sid].set_index("unit_seq")
        block = pd.DataFrame(index=rows.index)
        block[f"s{sid}_checklist_time_s"] = rows["checklist_time_s"]
        block[f"s{sid}_checklist_pass"] = rows["checklist_pass"]
        block[f"s{sid}_operator_bucket"] = rows["operator_bucket"]

        nominal = cfg.station(sid).checklist_nominal_s or 1.0
        block[f"s{sid}_checklist_ratio"] = rows["checklist_time_s"] / nominal

        for code in sorted(ck["notes_code"].unique()):
            block[f"s{sid}_note_{code.lower()}"] = (rows["notes_code"] == code).astype(int)
        frames.append(block)

    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, how="outer")
    return out.reset_index()


def _lot_trailing_defect_rate(
    inspections: pd.DataFrame, prediction_ts: pd.Series
) -> pd.Series:
    """Rolling defect rate of the same lot, using ONLY inspections already completed.

    This is the feature the audit flagged (H10). Two rules make it honest:
      1. the rolling mean at row i uses inspections 1..i-1 (shift(1)), never row i itself
      2. a unit reads the value as of ITS prediction time, matched by merge_asof on
         inspection_ts -- so inspections that had not happened yet are invisible

    Selecting by unit index instead would pull in labels from 25-40 minutes in the future.
    """
    hist = inspections.sort_values("inspection_ts")[
        ["lot_id", "inspection_ts", "defect_detected"]
    ].copy()
    hist["lot_rate"] = (
        hist.groupby("lot_id")["defect_detected"]
        .transform(lambda s: s.shift(1).rolling(LOT_TRAILING_UNITS, min_periods=10).mean())
    )
    hist = hist.dropna(subset=["lot_rate"])[["lot_id", "inspection_ts", "lot_rate"]]

    query = prediction_ts.reset_index()
    query.columns = ["unit_seq", "prediction_ts"]
    query = query.merge(
        inspections[["unit_seq", "lot_id"]], on="unit_seq", how="left"
    ).sort_values("prediction_ts")

    merged = pd.merge_asof(
        query,
        hist.sort_values("inspection_ts"),
        left_on="prediction_ts",
        right_on="inspection_ts",
        by="lot_id",
        direction="backward",
        allow_exact_matches=False,
    )
    return merged.set_index("unit_seq")["lot_rate"]


def build_features(
    cfg: PlantConfig,
    telemetry: pd.DataFrame,
    checklists: pd.DataFrame,
    inspections: pd.DataFrame,
) -> FeatureSet:
    cutoff = cfg.prediction_cutoff_station()
    tel = telemetry[telemetry["station_id"] <= cutoff].copy()

    # Prediction time = when the unit leaves the cutoff station. Everything after this
    # instant is the future.
    pred_ts = (
        tel[tel["station_id"] == cutoff].set_index("unit_seq")["exit_ts"].sort_index()
    )

    sig = _station_signal_features(cfg, tel, cutoff)
    flow = _flow_features(cfg, tel, cutoff)
    ck = _checklist_features(cfg, checklists, cutoff)

    X = sig.merge(flow, on="unit_seq", how="outer").merge(ck, on="unit_seq", how="outer")
    X = X[X["unit_seq"].isin(pred_ts.index)].set_index("unit_seq").sort_index()

    insp = inspections.set_index("unit_seq").reindex(X.index)
    X["lot_trailing_defect_rate"] = _lot_trailing_defect_rate(
        inspections, pred_ts.reindex(X.index)
    ).reindex(X.index)

    # Categorical context, encoded numerically for the tree model.
    variant = telemetry.groupby("unit_seq")["variant"].first().reindex(X.index)
    for v in cfg.variant_ids:
        X[f"variant_{v}"] = (variant == v).astype(int)

    first_rows = telemetry.sort_values("entry_ts").groupby("unit_seq").first().reindex(X.index)
    for sh in cfg.shifts:
        X[f"shift_{sh.id}"] = (first_rows["shift_id"] == sh.id).astype(int)

    ts = pred_ts.reindex(X.index)
    day = (ts // 86400).astype(int) + 1
    hour = (ts / 3600.0) % 24.0
    X["hour_of_day"] = hour.to_numpy()
    X["day_of_week"] = ((day - 1 + 0) % 7).to_numpy()

    meta = pd.DataFrame(
        {
            "unit_seq": X.index,
            "unit_id": insp["unit_id"].to_numpy(),
            "prediction_ts": ts.to_numpy(),
            "inspection_ts": insp["inspection_ts"].to_numpy(),
            "variant": variant.to_numpy(),
            "lot_id": insp["lot_id"].to_numpy(),
            "shift_id": first_rows["shift_id"].to_numpy(),
            "day": day.to_numpy(),
            "split": _split_for_day(cfg, day.to_numpy()),
            "plant_id": cfg.plant_id,
        }
    )

    y = insp["defect_detected"].fillna(0).to_numpy().astype(np.int8)
    y_true = insp["true_defect"].fillna(0).to_numpy().astype(np.int8)
    mode = insp["defect_mode"].fillna("").to_numpy()

    X = X.astype(np.float32)
    return FeatureSet(X=X, meta=meta, y=y, y_true=y_true, defect_mode=mode)


def feature_station_ids(names: list[str]) -> dict[str, int]:
    """Extract the station id encoded in each `s<N>_...` feature name."""
    out: dict[str, int] = {}
    for n in names:
        if n.startswith("s") and "_" in n:
            head = n.split("_", 1)[0][1:]
            if head.isdigit():
                out[n] = int(head)
    return out
