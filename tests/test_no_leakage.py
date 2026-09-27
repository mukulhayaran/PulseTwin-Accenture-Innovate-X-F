"""Leakage tests.

The original spec's guard was a scan of column names for station IDs greater than the
cutoff. That catches the obvious mistake and is kept below as a cheap secondary check --
but it is structurally incapable of catching the leak that actually matters, because a
name says nothing about which timestamps a value was computed from.

The primary guard here is a PERTURBATION TEST: corrupt everything that happens after a
chosen instant, rebuild the features, and assert that units whose prediction time precedes
that instant are bit-identical. A feature that reads the future cannot survive it.

This is what protects `lot_trailing_defect_rate` (docs/AUDIT.md H10). Inspection happens
~9-11 minutes (measured) after the prediction point, so selecting "the last 100 units of this lot" by
index silently ingests labels that do not exist yet -- invisibly, and while inflating
PR-AUC enough to trip the spec's own "simulator is too clean" rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from offline.config_schema import load_plant_config
from offline.ml.featurize import build_features, feature_station_ids

DATA = "data/plant_a"


@pytest.fixture(scope="module")
def raw():
    cfg = load_plant_config("plant_a.yaml")
    tel = pd.read_parquet(f"{DATA}/telemetry.parquet")
    ck = pd.read_parquet(f"{DATA}/checklists.parquet")
    ins = pd.read_parquet(f"{DATA}/inspections.parquet")
    return cfg, tel, ck, ins


@pytest.fixture(scope="module")
def features(raw):
    cfg, tel, ck, ins = raw
    return build_features(cfg, tel, ck, ins)


# ------------------------------------------------------------------ the primary guard


def test_future_data_cannot_change_past_features(raw, features):
    """Corrupt everything after an instant T; features for units predicted before T must
    be unchanged.

    This is a causal test, not a syntactic one. It fails for ANY feature that reads
    forward in time, whatever the column is called.
    """
    cfg, tel, ck, ins = raw
    base = features

    cut = float(np.median(base.meta["prediction_ts"]))

    tel2 = tel.copy()
    future_rows = tel2["entry_ts"] > cut
    for col in ("torque_nm", "vibration_mm_s", "temperature_c", "weld_current_a",
                "humidity_pct", "film_thickness_um", "cycle_s", "buffer_wait_s"):
        tel2.loc[future_rows, col] = tel2.loc[future_rows, col] * -7.5 + 999.0

    ck2 = ck.copy()
    ck2.loc[ck2["ts"] > cut, "checklist_time_s"] = 999.0
    ck2.loc[ck2["ts"] > cut, "checklist_pass"] = 0

    # The one that matters: flip every inspection outcome recorded after T.
    ins2 = ins.copy()
    ins2.loc[ins2["inspection_ts"] > cut, "defect_detected"] = 1

    perturbed = build_features(cfg, tel2, ck2, ins2)

    safe = base.meta["prediction_ts"].to_numpy() <= cut
    assert safe.sum() > 1000, "not enough pre-cutoff units to make the test meaningful"

    a = base.X.loc[safe].reset_index(drop=True)
    b = perturbed.X.loc[safe].reset_index(drop=True)

    drifted = [c for c in a.columns if not a[c].equals(b[c])]
    assert not drifted, f"features changed when only FUTURE data was corrupted: {drifted}"


def test_lot_feature_is_not_selected_by_index(raw, features):
    """Directly target H10: the lot rate must lag the prediction time.

    An index-selected window would pull in inspections that had not happened yet. Here
    every unit's value must be reproducible from inspections strictly completed before
    its own prediction instant.
    """
    cfg, tel, ck, ins = raw
    col = features.X["lot_trailing_defect_rate"]
    meta = features.meta.set_index("unit_seq")

    hist = ins[["unit_seq", "lot_id", "inspection_ts", "defect_detected"]].sort_values(
        "inspection_ts"
    )

    checked = 0
    for unit_seq in features.X.index[::911]:  # sparse sample -- this loop is O(n) each
        val = col.loc[unit_seq]
        if not np.isfinite(val):
            continue
        pred_ts = meta.loc[unit_seq, "prediction_ts"]
        lot = meta.loc[unit_seq, "lot_id"]

        past = hist[(hist["lot_id"] == lot) & (hist["inspection_ts"] < pred_ts)]
        if len(past) < 10:
            continue
        # merge_asof anchors on the most recent completed inspection, whose own rolling
        # value excludes itself -- so reproduce exactly that.
        expected = past["defect_detected"].iloc[:-1].tail(100).mean()
        assert abs(val - expected) < 1e-6, (
            f"unit {unit_seq}: lot rate {val} != {expected} computed from completed "
            f"inspections only"
        )
        checked += 1
    assert checked >= 5, "sample found nothing to verify"


def test_no_feature_uses_the_units_own_inspection(raw, features):
    """A unit's own outcome must never reach its own features."""
    cfg, tel, ck, ins = raw
    ins2 = ins.copy()
    ins2["defect_detected"] = 1 - ins2["defect_detected"]
    ins2["true_defect"] = 1 - ins2["true_defect"]

    flipped = build_features(cfg, tel, ck, ins2)

    # lot_trailing_defect_rate legitimately depends on OTHER units' outcomes, so exclude
    # it here; the perturbation test above already proves it only reads the past.
    cols = [c for c in features.X.columns if c != "lot_trailing_defect_rate"]
    pd.testing.assert_frame_equal(features.X[cols], flipped.X[cols])


# -------------------------------------------------------------------- secondary guards


def test_no_feature_comes_from_beyond_the_cutoff(raw, features):
    """The original spec's name scan. Cheap, and still worth having."""
    cfg, _, _, _ = raw
    cutoff = cfg.prediction_cutoff_station()
    offenders = {
        name: sid for name, sid in feature_station_ids(features.feature_names).items()
        if sid > cutoff
    }
    assert not offenders, f"features reference stations beyond S{cutoff}: {offenders}"


def test_target_columns_are_not_in_the_feature_matrix(features):
    banned = {"defect_detected", "true_defect", "defect_mode", "propensity", "escaped",
              "false_reject", "latent_lot_quality", "latent_paint_batch_weak"}
    assert banned.isdisjoint(set(features.X.columns))


def test_splits_are_temporally_ordered(features):
    """Temporal split only. A random split would leak the future everywhere."""
    m = features.meta
    tr = m.loc[m["split"] == "train", "prediction_ts"]
    va = m.loc[m["split"] == "validate", "prediction_ts"]
    te = m.loc[m["split"] == "test", "prediction_ts"]
    assert tr.max() < va.min() < va.max() < te.min()


def test_every_split_contains_peak_days(features):
    """ADR-020 amended.

    At 21 days the test split was Thu-Sun with no EV-heavy day in it -- so it could not
    evaluate the very scenario the project is about, and held only 86 positives.
    """
    cfg = load_plant_config("plant_a.yaml")
    peak = set(cfg.ambient.humidity_high_days)
    m = features.meta.copy()
    m["weekday"] = (m["day"] - 1) % 7
    for split in ("train", "validate", "test"):
        days = set(m.loc[m["split"] == split, "weekday"])
        assert days & peak, f"{split} split contains no peak day"


def test_test_split_has_enough_positives(features):
    """ADR-020. Too few positives and the PR-AUC CI is wider than the target band."""
    m = features.meta["split"].to_numpy()
    positives = features.y[m == "test"].sum()
    assert positives >= 150, f"test split has only {positives} positives"
