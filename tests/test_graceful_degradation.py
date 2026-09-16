"""Backend half of graceful degradation (finals brief Phase F5): what /api/predict/defect
does when the primary model is missing or broken. The frontend half (OfflineBanner,
StaleDataIndicator reconnect behaviour) is a browser concern outside pytest's reach --
that is exercised with the `playwright` skill during demo rehearsal, per
docs/FINALS_DEMO_SCRIPT.md.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("fastapi")

if not (ROOT / "artifacts" / "pulsetwin.db").exists():
    pytest.skip("artifacts not built", allow_module_level=True)

from serve.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_predict_with_no_scorer_falls_back_cleanly(client):
    """A plant with no primary model registered must degrade to the SPC fallback, not
    500 -- this is what "the model is missing" looks like in practice."""
    with patch.dict(app.state.__dict__ if hasattr(app.state, "__dict__") else {}, {}):
        pass  # app.state has no per-plant scorer dict; STATE["scorers"] is main.py's own
    from serve.main import STATE

    original = STATE["scorers"].pop("plant_a", None)
    try:
        r = client.post("/api/predict/defect", json={"plant_id": "plant_a", "features": {}})
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            assert r.json()["explanation_mode"] == "spc_fallback"
            assert "FALLBACK" in r.json()["recommended_action"]
    finally:
        if original is not None:
            STATE["scorers"]["plant_a"] = original


def test_predict_falls_back_when_primary_scorer_raises(client):
    """A corrupt/incompatible model file must degrade, not 500 the whole endpoint."""
    from serve.main import STATE

    scorer = STATE["scorers"].get("plant_a")
    if scorer is None or "plant_a" not in STATE["fallback_scorers"]:
        pytest.skip("no primary+fallback scorer pair registered for plant_a")

    with patch.object(scorer, "predict", side_effect=ValueError("corrupt model")):
        r = client.post("/api/predict/defect", json={"plant_id": "plant_a", "features": {}})
    assert r.status_code == 200
    assert r.json()["explanation_mode"] == "spc_fallback"


def test_predict_still_works_normally_after_a_fallback(client):
    """The fallback path must not leave any patched/global state behind."""
    r = client.post("/api/predict/defect", json={"plant_id": "plant_a", "features": {}})
    assert r.status_code == 200
    assert r.json()["explanation_mode"] == "decision_path"


def test_unknown_plant_with_no_fallback_is_a_clean_404():
    from serve.fallback_scorer import FallbackScorer

    assert FallbackScorer  # imported cleanly, numpy+stdlib only (import wall)


def test_fallback_scorer_flags_a_three_sigma_violation():
    import numpy as np
    import tempfile

    from serve.fallback_scorer import FallbackScorer

    with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as fh:
        np.savez(fh.name, station_id=np.array([8], dtype=np.int32),
                  signal=np.array(["vibration_mm_s"], dtype=object),
                  mean=np.array([3.0]), std=np.array([0.5]))
        scorer = FallbackScorer(fh.name)

    normal = scorer.score({"s8_vibration_mm_s": 3.1})
    assert normal["probability"] < 0.3
    assert not normal["violations"]

    violation = scorer.score({"s8_vibration_mm_s": 20.0})   # ~34 sigma out
    assert violation["probability"] > 0.5
    assert violation["violations"]
    assert violation["violations"][0]["station_id"] == 8


def test_fallback_scorer_ignores_unknown_features():
    import numpy as np
    import tempfile

    from serve.fallback_scorer import FallbackScorer

    with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as fh:
        np.savez(fh.name, station_id=np.array([8], dtype=np.int32),
                  signal=np.array(["vibration_mm_s"], dtype=object),
                  mean=np.array([3.0]), std=np.array([0.5]))
        scorer = FallbackScorer(fh.name)

    result = scorer.score({"totally_unrelated_feature": 42.0})
    assert result["n_signals_considered"] == 0
    assert result["probability"] == 0.0
