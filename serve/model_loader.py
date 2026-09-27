"""Which model answers /api/predict/defect for a plant (ADR-061).

Kept out of serve/scorer.py, whose imports are pinned to numpy + pathlib by
tests/test_serve_imports.py -- the scorer is the file most likely to drift toward sklearn,
and a loader that reads JSON does not belong in it.
"""

from __future__ import annotations

import json
from pathlib import Path

from serve.rolling_threshold import TrailingBudgetThreshold
from serve.scorer import Scorer
from serve.xgb_scorer import XGBScorer


def load_scorer(artifacts: Path, plant_id: str):
    """The plant's CHAMPION scorer, as named by artifacts/model_selection_<plant>.json
    (ADR-061), or the committed HistGradientBoosting .npz when no selection file exists.

    Returns (scorer, info) or (None, {}) when no model artifact is present. `info` is what
    the API reports about the model answering: type, version, calibration, and whether a
    challenger was evaluated and why it was not promoted. The selection file is the only
    way the XGBoost scorer is ever chosen, and offline/ml/xgb_challenger.py writes
    `champion: "xgboost"` only when every promotion gate passed on the untouched test set.
    """
    selection_path = artifacts / f"model_selection_{plant_id}.json"
    selection = {}
    if selection_path.exists():
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            selection = {}
    info = {"challenger": selection.get("challenger")} if selection else {}
    if selection.get("champion") == "xgboost":
        path = artifacts / str(selection["champion_artifact"])
        scorer = XGBScorer(path)
        policy = scorer.threshold_policy or {}
        if policy.get("type") == "trailing_budget":
            # ADR-062: the alert cut follows the budget over a trailing window of scored
            # units, seeded with the validation week the model was thresholded on.
            info = {**info, "rolling": TrailingBudgetThreshold(
                policy["seed_scores"], policy["window_units"], policy["budget_alerts"]),
                "threshold_policy": "trailing_budget"}
        return scorer, {**info, "model_id": f"{plant_id}_defect_risk_xgboost",
                        "model_type": scorer.model_type, "version": 2,
                        "model_version": scorer.model_version,
                        "calibration_method": scorer.calibration_method,
                        "alert_threshold": scorer.alert_threshold, "artifact": str(path)}
    npz = artifacts / f"model_{plant_id}.npz"
    if not npz.exists():
        return None, {}
    scorer = Scorer(npz)
    return scorer, {**info, "model_id": f"{plant_id}_defect_risk_committed",
                    "model_type": scorer.model_type, "version": 1, "model_version": "1",
                    "calibration_method": scorer.calibration_method, "artifact": str(npz)}
