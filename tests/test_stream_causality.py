"""ADR-063: no stream event may show a risk or an alert before the unit was scored.

Every replay/live event used to carry the unit's FINAL calibrated risk and alert from
station 1 on -- a prediction shown ~20 stations before the prediction cutoff could have
produced it. The model and its features were causal; the stream presenting them was not.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


@pytest.mark.parametrize("plant", ["plant_a", "plant_b"])
def test_risk_appears_only_from_the_prediction_time(plant):
    path = ARTIFACTS / f"stream_{plant}.jsonl"
    if not path.exists():
        pytest.skip("stream not built")
    scored = unscored = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            e = json.loads(line)
            if e.get("calibrated_risk") is None:
                unscored += 1
                assert e.get("alert", 0) == 0, f"alert before scoring: {e['unit_id']} S{e['station_id']}"
                continue
            scored += 1
            assert e["prediction_ts"] and e["lead_time_min"] is not None
            assert datetime.fromisoformat(e["ts"]) >= datetime.fromisoformat(e["prediction_ts"])
    assert scored and unscored        # both halves of the line are present in the window
