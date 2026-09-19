"""Structured champion-vs-challenger comparison report.

    from mlops.evaluation import compare

Finals brief's promotion rule: a challenger must beat the champion on PR-AUC AND
precision@budget AND calibration (docs/MLOPS_LIFECYCLE.md) -- all three, not a weighted
average of them, so one worse metric cannot be hidden behind two better ones.
"""

from __future__ import annotations


def _pull(metrics: dict) -> dict:
    thr = metrics.get("thresholds", {})
    reliability = metrics.get("reliability", [])
    max_dev = max(
        (abs(b["predicted"] - b["observed"]) for b in reliability), default=0.0
    ) * 100.0
    return {
        "pr_auc": metrics.get("pr_auc"),
        "precision_at_budget": thr.get("precision_at_budget"),
        "recall_at_budget": thr.get("recall_at_budget"),
        "calibration_max_deviation_pp": round(max_dev, 2),
    }


def _delta(champion: dict, challenger: dict, key: str) -> float | None:
    if champion.get(key) is None or challenger.get(key) is None:
        return None
    return round(challenger[key] - champion[key], 4)


def compare(champion_metrics: dict, challenger_metrics: dict, champion_id: str,
            challenger_id: str) -> dict:
    c = _pull(champion_metrics)
    ch = _pull(challenger_metrics)

    # Deliberately asymmetric: STRICTLY better on PR-AUC (the headline metric -- an
    # identical model must not be promotable), NOT WORSE on the two guardrails. So an
    # exact tie reports beats_pr_auc=False alongside the two guardrails passing, and the
    # recommendation is REJECT. That combination looks contradictory in the report if you
    # read the guardrail keys as "beat"; `beats_precision_at_budget` is really
    # "did not regress", the same test `beats_or_matches_calibration` names honestly.
    beats_pr_auc = (c["pr_auc"] is not None and ch["pr_auc"] is not None
                    and ch["pr_auc"] > c["pr_auc"])
    beats_precision = (c["precision_at_budget"] is not None
                        and ch["precision_at_budget"] is not None
                        and ch["precision_at_budget"] >= c["precision_at_budget"])
    # Calibration: lower deviation is better, so the challenger must not be worse.
    beats_calibration = ch["calibration_max_deviation_pp"] <= c["calibration_max_deviation_pp"]

    promote = beats_pr_auc and beats_precision and beats_calibration

    return {
        "champion": {"model_id": champion_id, **c},
        "challenger": {"model_id": challenger_id, **ch},
        "improvement": {
            "pr_auc": _delta(c, ch, "pr_auc"),
            "precision_at_budget": _delta(c, ch, "precision_at_budget"),
            "calibration_max_deviation_pp": _delta(c, ch, "calibration_max_deviation_pp"),
        },
        "criteria": {
            "beats_pr_auc": beats_pr_auc,
            "beats_precision_at_budget": beats_precision,
            "beats_or_matches_calibration": beats_calibration,
        },
        "recommendation": "PROMOTE" if promote else "REJECT",
        "requires_human_approval": True,
    }
