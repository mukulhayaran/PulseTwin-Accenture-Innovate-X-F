"""Apply a PROMOTED champion to everything offline bakes (ADR-061/062).

`artifacts/model_selection_<plant>.json` names the champion. While it is the
HistGradientBoosting model (the committed state), `promoted_xgb()` returns None and every
caller behaves exactly as before. After an XGBoost promotion, build_artifact and the live
twin score with it: unit risks, alert flags (under the model's own trailing-window policy),
explanations, the plant threshold and the headline metrics all follow the champion.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def selection(artifacts: Path, plant_id: str) -> dict:
    path = artifacts / f"model_selection_{plant_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def promoted_xgb(artifacts: Path, plant_id: str):
    """The XGBoost champion's scorer, or None while HGB is champion."""
    sel = selection(artifacts, plant_id)
    if sel.get("champion") != "xgboost":
        return None
    from serve.xgb_scorer import XGBScorer
    return XGBScorer(artifacts / str(sel["champion_artifact"]))


def rescore(scorer, scores: pd.DataFrame, feats: pd.DataFrame,
            cost_threshold: float) -> pd.DataFrame:
    """Replace the unit scores and alert flags with the XGBoost champion's. Alerts follow
    its trailing-window budget in prediction-time order, seeded exactly as at runtime."""
    from serve.rolling_threshold import TrailingBudgetThreshold

    out = scores.copy()
    X = feats.reindex(out["unit_seq"].to_numpy()).to_numpy(dtype=np.float64)
    out["risk_raw"] = scorer.predict_raw_proba(X)
    out["calibrated_risk"] = scorer.predict(X)
    out["above_cost_threshold"] = (out["calibrated_risk"] >= cost_threshold).astype(int)
    policy = scorer.threshold_policy or {}
    if policy.get("type") == "trailing_budget":
        roll = TrailingBudgetThreshold(policy["seed_scores"], policy["window_units"],
                                       policy["budget_alerts"])
        flags = np.zeros(len(out), dtype=int)
        for i in np.argsort(out["prediction_ts"].to_numpy(), kind="stable"):
            v = float(out["calibrated_risk"].iat[i])
            flags[i] = int(v >= roll.threshold)
            roll.observe(v)
        out["alert_fired"] = flags
    else:
        out["alert_fired"] = (out["calibrated_risk"] >= scorer.alert_threshold).astype(int)
    return out


def headline_metrics(artifacts: Path, plant_id: str) -> dict:
    """The promoted challenger's measured test numbers, from its own report, to replace
    the HGB figures in the baked `metrics` table."""
    rep = json.loads((artifacts / f"xgb_challenger_{plant_id}.json").read_text(encoding="utf-8"))
    t = rep["challenger"].get("test_trailing_budget") or rep["challenger"]["test"]
    return {"pr_auc": t["pr_auc"], "roc_auc": t["roc_auc"], "brier": t["brier"],
            "precision_at_budget": t["precision"], "recall_at_budget": t["recall"],
            "alerts_per_shift": t["alerts_per_shift"]}
