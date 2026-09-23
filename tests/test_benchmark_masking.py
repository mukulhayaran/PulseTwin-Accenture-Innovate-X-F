"""The sensor-coverage masking engine: never mutates input, RANDOM/STATION_LEVEL behave
as documented, invalid input fails loudly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from benchmark import masking


@pytest.fixture
def X() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    return pd.DataFrame(rng.normal(size=(200, 10)), columns=[f"f{i}" for i in range(10)])


def test_coverage_100_is_a_true_noop(X) -> None:
    out = masking.mask(X, 100, masking.RANDOM)
    pd.testing.assert_frame_equal(out, X)


def test_masking_never_mutates_the_input(X) -> None:
    before = X.copy()
    masking.mask(X, 50, masking.RANDOM)
    pd.testing.assert_frame_equal(X, before)


def test_random_masking_drops_roughly_the_requested_fraction(X) -> None:
    out = masking.mask(X, 60, masking.RANDOM, seed=42)  # 40% should become NaN
    frac_missing = out.isna().to_numpy().mean()
    assert 0.30 < frac_missing < 0.50, frac_missing


def test_random_masking_is_deterministic_for_a_fixed_seed(X) -> None:
    a = masking.mask(X, 50, masking.RANDOM, seed=7)
    b = masking.mask(X, 50, masking.RANDOM, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_random_masking_differs_across_seeds(X) -> None:
    a = masking.mask(X, 50, masking.RANDOM, seed=1)
    b = masking.mask(X, 50, masking.RANDOM, seed=2)
    assert not a.equals(b)


def test_invalid_coverage_raises(X) -> None:
    with pytest.raises(ValueError):
        masking.mask(X, 150, masking.RANDOM)
    with pytest.raises(ValueError):
        masking.mask(X, -1, masking.RANDOM)


def test_unknown_strategy_raises(X) -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        masking.mask(X, 50, "not_a_real_strategy")


def test_station_level_requires_station_of(X) -> None:
    with pytest.raises(ValueError, match="station_of"):
        masking.mask(X, 50, masking.STATION_LEVEL)


def test_station_level_drops_whole_stations_not_individual_cells() -> None:
    X = pd.DataFrame(
        np.ones((50, 6)), columns=["a1", "a2", "b1", "b2", "c1", "c2"])
    station_of = {"a1": "S1", "a2": "S1", "b1": "S2", "b2": "S2", "c1": "S3", "c2": "S3"}
    out = masking.mask(X, 66, masking.STATION_LEVEL, station_of=station_of, seed=42)
    # 34% drop of 3 stations -> exactly 1 station dropped, so exactly 2 columns are
    # entirely NaN and the other 4 are entirely untouched -- never a partial column.
    fully_null_cols = [c for c in out.columns if out[c].isna().all()]
    fully_kept_cols = [c for c in out.columns if out[c].notna().all()]
    assert len(fully_null_cols) + len(fully_kept_cols) == 6
    # Columns belonging to the same station are always dropped together.
    for station in ("S1", "S2", "S3"):
        cols = [c for c, s in station_of.items() if s == station]
        states = {c in fully_null_cols for c in cols}
        assert len(states) == 1, f"station {station} was only partially dropped"


def test_coverage_sweep_returns_the_configured_levels() -> None:
    assert masking.coverage_sweep() == [100, 80, 60, 40, 20]
