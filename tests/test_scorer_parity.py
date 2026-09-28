"""Numpy scorer parity with sklearn.

The serving layer cannot import sklearn, so the trained ensemble is flattened into numpy
arrays and walked by serve/scorer.py. That rewrite is only trustworthy if it is proven
identical on real data, not spot-checked -- so this scores the ENTIRE test split with
both and requires max |difference| < 1e-6.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from serve.scorer import Scorer

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
DATA = ROOT / "data"

PLANTS = ["plant_a"]


def _load(plant: str):
    npz = ARTIFACTS / f"model_{plant}.npz"
    pkl = DATA / plant / "model.pkl"
    feats = DATA / plant / "features.parquet"
    meta = DATA / plant / "feature_meta.parquet"
    if not (npz.exists() and pkl.exists() and feats.exists()):
        pytest.skip(f"{plant} artifacts not built")
    with pkl.open("rb") as fh:
        bundle = pickle.load(fh)
    X = pd.read_parquet(feats)
    m = pd.read_parquet(meta)
    te = (m["split"] == "test").to_numpy()
    return Scorer(npz), bundle["model"], X[te], bundle["feature_names"]


@pytest.mark.parametrize("plant", PLANTS)
def test_feature_order_matches(plant: str) -> None:
    """A silent column reorder would produce plausible-looking wrong numbers."""
    scorer, _, X, names = _load(plant)
    assert scorer.feature_names == list(X.columns) == list(names)


@pytest.mark.parametrize("plant", PLANTS)
def test_calibrated_parity_on_full_test_set(plant: str) -> None:
    scorer, model, X, _ = _load(plant)
    sk = model.predict(X)
    npy = scorer.predict(X.to_numpy(dtype=np.float64))
    diff = float(np.max(np.abs(sk - npy)))
    assert diff < 1e-6, f"{plant}: max calibrated difference {diff:.3e}"


@pytest.mark.parametrize("plant", PLANTS)
def test_raw_parity_on_full_test_set(plant: str) -> None:
    scorer, model, X, _ = _load(plant)
    sk = model.predict_raw(X)
    npy = scorer.predict_raw_proba(X.to_numpy(dtype=np.float64))
    diff = float(np.max(np.abs(sk - npy)))
    assert diff < 1e-6, f"{plant}: max raw difference {diff:.3e}"


@pytest.mark.parametrize("plant", PLANTS)
def test_explanations_are_additive(plant: str) -> None:
    """Decision-path contributions must sum to (raw score - expected value), exactly.

    That additivity is what makes the explanation honest: the numbers on the alert card
    account for the whole prediction, with nothing unexplained.
    """
    scorer, _, X, _ = _load(plant)
    rows = X.to_numpy(dtype=np.float64)[:40]
    for x in rows:
        full = scorer.explain(x, top=len(scorer.feature_names))
        total = sum(f["contribution"] for f in full)
        expected = float(scorer.raw_score(x.reshape(1, -1))[0]) - scorer.expected_value
        assert abs(total - expected) < 1e-6, f"contributions {total} != {expected}"


@pytest.mark.parametrize("plant", PLANTS)
def test_missing_features_do_not_crash(plant: str) -> None:
    """A partial feature vector must route through the ensemble's native NaN handling."""
    scorer, _, _, _ = _load(plant)
    x = scorer.vector_from({"paint_buffer_dwell_s": 620.0})
    p = float(scorer.predict(x.reshape(1, -1))[0])
    assert 0.0 <= p <= 1.0
