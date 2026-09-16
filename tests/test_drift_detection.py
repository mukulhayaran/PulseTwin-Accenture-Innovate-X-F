"""mlops/drift_detector.py -- PSI, calibration drift, sensor coverage, against the real
committed simulated data (data/plant_a). Offline-tier: allowed to use pandas.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]

if not (ROOT / "data" / "plant_a" / "features.parquet").exists():
    pytest.skip("simulated data not generated -- run `make generate train`",
                allow_module_level=True)

from mlops.drift_detector import calibration_drift, compute_psi, run, sensor_coverage  # noqa: E402


def test_psi_is_zero_for_identical_distributions():
    rng = np.random.default_rng(42)
    x = rng.normal(size=2000)
    assert compute_psi(x, x) < 1e-6


def test_psi_is_large_for_a_shifted_distribution():
    rng = np.random.default_rng(42)
    ref = rng.normal(loc=0.0, size=2000)
    shifted = rng.normal(loc=5.0, size=500)   # far outside the reference's range
    assert compute_psi(ref, shifted) > 0.2


def test_psi_handles_empty_current_gracefully():
    ref = np.array([1.0, 2.0, 3.0, 4.0, 5.0] * 20)
    assert compute_psi(ref, np.array([])) == 0.0


def test_calibration_drift_flags_a_badly_miscalibrated_model():
    # Predicts near-zero everywhere but half the units actually defect -- max deviation
    # should sit near 50 percentage points in the low bin.
    predicted = np.full(200, 0.05)
    actual = np.zeros(200)
    actual[:100] = 1
    result = calibration_drift(predicted, actual)
    assert result["max_deviation_pp"] > 40.0


def test_calibration_drift_is_near_zero_for_a_well_calibrated_model():
    rng = np.random.default_rng(42)
    predicted = rng.uniform(0.0, 1.0, size=5000)
    actual = (rng.uniform(size=5000) < predicted).astype(float)
    result = calibration_drift(predicted, actual)
    assert result["max_deviation_pp"] < 15.0


def test_sensor_coverage_full_for_a_dense_frame():
    import pandas as pd
    df = pd.DataFrame(np.random.default_rng(0).normal(size=(100, 20)))
    cov = sensor_coverage(df)
    assert cov["full"] == 1.0
    assert cov["manual"] == 0.0


def test_sensor_coverage_flags_sparse_rows_as_manual():
    import pandas as pd
    df = pd.DataFrame(np.full((10, 20), np.nan))
    cov = sensor_coverage(df)
    assert cov["manual"] == 1.0


def test_drift_run_against_real_artifacts_produces_a_valid_report():
    report = run("configs/plant_a.yaml", model_id="test_drift_model")
    assert report["plant_id"] == "plant_a"
    assert report["overall_status"] in ("healthy", "warning", "degraded")
    psi = report["psi_summary"]
    assert 0 <= psi["features_shifted"] <= psi["features_total"]
    assert report["calibration"]["max_deviation_pp"] >= 0.0
    assert 0.0 <= report["sensor_coverage"]["full"] <= 1.0
